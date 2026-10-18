"""Hosted demo runner: lets a judge without a wallet or testnet ETH trigger the PRD scenarios from the web UI.

Enabled only by DEMO_RUNNER=1 on chain 31337 or 84532 (see config.Config). The service signs with DEMO-ONLY keys taken from the
environment, so anyone who can reach POST /demo/run/* can make it spend gas from them: keep those accounts empty of
anything valuable. The scenario logic is the same as the headless demo (attestor/demo.py), via attestor/scenarios.py:

  green         honest EAF report -> attest -> green / Attested
  red           implausible BF-BOF report -> attest -> red / PendingVerifier -> verifier rejects with a stored reason
  double-claim  green attest -> allocate part to importer A -> over-allocate to importer B: DoubleClaimBlocked, sent with a
                fixed gas limit so it is MINED with status 0 and has a real explorer link
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from eth_account import Account

from .chain import ChainError
from .config import DemoRunnerConfig
from .scenarios import DEMO_REPORTS_DIR, clamp_to_current_month, consignment_id, free_period, load_report, run_id, sign_report
from .service import Attestor, ServiceError

log = logging.getLogger("attestor.demo_runner")

SCENARIOS = ("green", "red", "double-claim")
FAILED_ALLOCATE_GAS = 300_000  # fixed limit: skips gas estimation so the reverting allocate() is mined with status 0
ALLOCATE_SHARE = (3, 5)  # part allocated to importer A; the rest (2/5) cannot cover a second request of the same size


class StepFailed(Exception):
    pass


class DemoRunner:
    def __init__(self, *, attestor: Attestor, cfg: DemoRunnerConfig, reports_dir=DEMO_REPORTS_DIR):
        self.attestor, self.cfg, self.reports_dir = attestor, cfg, reports_dir
        self.lock = threading.Lock()  # one run at a time, globally (nonce management + keeps the chain readable)
        self._exporter_keys = [k for k in (cfg.exporter_key, cfg.exporter_b_key) if k]

    # ------------------------------------------------------------------ helpers
    @property
    def chain(self):
        return self.attestor.chain

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.attestor._clock(), timezone.utc)

    def _exporter_key(self, installation: dict) -> str:
        for key in self._exporter_keys:
            if Account.from_key(key).address == installation["operator"]:
                return key
        raise StepFailed(f"none of the configured demo exporter keys is the registered operator of {installation['id']} "
                         f"({installation['operator']})")

    def _prepare(self, name: str, steps: list[dict]) -> tuple[dict, str]:
        """Load the demo report, pick an unused period (earlier months only; bands are period-independent for the demo
        installations, proven in tests) and the key of the on-chain operator."""
        report = load_report(name, self.reports_dir)
        installation = self.chain.installation(report["installationId"])
        if installation is None:
            raise StepFailed(f"demo installation {report['installationId']} is not registered on this chain")
        now = self._now()
        start = clamp_to_current_month(report["period"], now.year, now.month)
        try:
            chosen = free_period(self.chain.batch_of, lambda bid: self.chain.batch(bid)["status"], {**report, "period": start})
        except LookupError as exc:
            raise StepFailed(str(exc)) from exc
        return chosen, self._exporter_key(installation)

    @staticmethod
    def _step(steps: list[dict], label: str, *, ok: bool = True, expected: bool = True, tx: str | None = None,
              detail: str = "") -> None:
        steps.append({"label": label, "ok": ok, "expected": expected, "txHash": tx, "detail": detail})

    def _attest(self, steps: list[dict], report: dict, key: str, want_band: str, want_status: str) -> dict:
        self._step(steps, f"Exporter signs the {report['installationId']} report for {report['period']} (EIP-191)",
                   detail=f"SEE {report['seeTco2PerT']} tCO2/t, {report['productionTonnes']:g} t")
        try:
            res = self.attestor.attest(report, sign_report(report, key))
        except ServiceError as exc:
            self._step(steps, "Attestor validates, scores and attests", ok=False, expected=False, detail=exc.detail)
            raise StepFailed(exc.detail) from exc
        except ChainError as exc:
            self._step(steps, "Attestor validates, scores and attests", ok=False, expected=False, detail=str(exc))
            raise StepFailed(str(exc)) from exc
        good = res["band"] == want_band and res["status"] == want_status
        self._step(steps, f"Attestor scores the report: risk {res['riskScore']} ({res['band']}) -> {res['status']}",
                   ok=True, expected=good, tx=res["txHash"],
                   detail=res["score"].get("explanation", ""))
        return res

    # ------------------------------------------------------------------ scenarios
    def _green(self, steps: list[dict]) -> dict:
        report, key = self._prepare("honest_eaf.json", steps)
        return self._attest(steps, report, key, "green", "Attested")

    def _red(self, steps: list[dict]) -> dict:
        report, key = self._prepare("implausible_bof.json", steps)
        res = self._attest(steps, report, key, "red", "PendingVerifier")
        batch_id = res["batchId"]
        text = (f"Declared SEE {report['seeTco2PerT']} tCO2/t is far below the plausible range for a {report['route']} "
                f"plant (risk {res['riskScore']}); rejected by the verifier. run {run_id()}")
        try:
            reason_hash = self.attestor.post_reason(batch_id, text)["reasonHash"]
            self._step(steps, "Verifier records the reason (stored off-chain, its hash goes on-chain)", detail=text)
            tx = self.chain.verifier_sign(batch_id, False, bytes.fromhex(reason_hash[2:]), self.cfg.verifier_key)
            status = self.chain.batch(batch_id)["status"]
            self._step(steps, f"Verifier calls verifierSign(batch {batch_id}, approve=false) -> {status}",
                       ok=True, expected=status == "Rejected", tx=tx.tx_hash)
        except (ChainError, ServiceError) as exc:
            detail = exc.detail if isinstance(exc, ServiceError) else str(exc)
            self._step(steps, "Verifier rejects the batch", ok=False, expected=False, detail=detail)
            raise StepFailed(detail) from exc
        return res

    def _double_claim(self, steps: list[dict]) -> dict:
        report, key = self._prepare("honest_eaf.json", steps)
        res = self._attest(steps, report, key, "green", "Attested")
        batch_id = res["batchId"]
        verified = int(self.chain.batch(batch_id)["verifiedTonnes"])
        part = verified * ALLOCATE_SHARE[0] // ALLOCATE_SHARE[1]
        cn, rid = int(report["cnCode"]), run_id()
        try:
            tx = self.chain.allocate(batch_id, consignment_id(batch_id, "A", rid), self.cfg.importer_a, cn, part, key)
            self._step(steps, f"Exporter allocates {part:,} of {verified:,} attested t to importer A", tx=tx.tx_hash,
                       detail=f"{verified - part:,} t remain")
        except ChainError as exc:
            self._step(steps, "Exporter allocates part of the batch to importer A", ok=False, expected=False, detail=str(exc))
            raise StepFailed(str(exc)) from exc
        try:
            revert, tx_hash = self.chain.allocate_expect_revert(
                batch_id, consignment_id(batch_id, "B", rid), self.cfg.importer_b, cn, part, key, gas=FAILED_ALLOCATE_GAS)
        except ChainError as exc:
            self._step(steps, f"Exporter tries to allocate the same {part:,} t to importer B", ok=False, expected=False,
                       detail=str(exc))
            raise StepFailed(str(exc)) from exc
        blocked = revert.name == "DoubleClaimBlocked"
        self._step(steps, f"Exporter tries to allocate {part:,} t more to importer B: only {verified - part:,} t remain",
                   ok=False, expected=blocked, tx=tx_hash, detail=str(revert))
        return res

    # ------------------------------------------------------------------ entry point
    def run(self, scenario: str) -> dict[str, Any]:
        """Run one scenario. The caller holds `self.lock`. Never raises for scenario failures: they are failed steps."""
        if scenario not in SCENARIOS:
            raise KeyError(scenario)
        steps: list[dict] = []
        res: dict | None = None
        try:
            res = {"green": self._green, "red": self._red, "double-claim": self._double_claim}[scenario](steps)
        except StepFailed as exc:
            log.warning("demo %s stopped: %s", scenario, exc)
            if not steps or steps[-1]["ok"]:
                self._step(steps, "Scenario could not run", ok=False, expected=False, detail=str(exc))
        except Exception:
            log.exception("demo %s crashed", scenario)
            self._step(steps, "Scenario crashed (see service log)", ok=False, expected=False, detail="internal error")
        batch_id = res["batchId"] if res else None
        return {
            "scenario": scenario,
            "chainId": self.chain.chain_id,
            "batchId": batch_id,
            "certificatePath": res["verifyPath"] if res else None,
            "steps": steps,
        }
