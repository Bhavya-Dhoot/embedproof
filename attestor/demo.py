"""Headless runner for the four PRD demo scenarios (PRD section 13), against a live Anvil + attestor.

    PYTHONPATH=. python -m attestor.demo [--attestor http://127.0.0.1:8000] [--rpc http://127.0.0.1:8545]

(a) honest EAF report -> green -> Attested, both signatures verify
(b) implausible BF-BOF report -> red -> PendingVerifier -> verifier rejects with a stored reason
(c) exporter A allocates 5,000 t to importer A (claimed), then the same 5,000 t to importer B
    -> reverts DoubleClaimBlocked(batchId, 5000, 0)
(d) tamper test: one edited digit flips commitment / ECDSA / ML-DSA checks; the untampered document passes
Scenario (a) ends by running the rescore CLI (`python -m attestor.rescore <certificate URL>`) on the green certificate.
Then a few hardening probes. Exit code is non-zero if any check fails. Safe to re-run on the same chain: used periods move
to the nearest earlier free month (shared logic: attestor/scenarios.py) and consignment ids are fresh.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
from eth_account.messages import encode_defunct
from eth_account import Account
from eth_utils import keccak

from . import bundle
from .canonical import keccak256
from .chain import Chain, ChainError, ChainRevert
from .config import REPO_ROOT, Config
from .scenarios import ADDR, KEYS, consignment_id, free_period, load_report, run_id, sign_report
from .signing import attestation_digest, recover_attestation_signer, select_backend
LATENCY_BUDGET_S = 15.0
ATTEST_GAS_BUDGET = 250_000
ALLOCATE_GAS_BUDGET = 120_000


@dataclass
class Scenario:
    name: str
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((label, bool(ok), detail))
        print(f"    [{'ok' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.checks) and all(ok for _, ok, _ in self.checks)


class Demo:
    def __init__(self, attestor_url: str, cfg: Config, reports_dir: Path):
        self.http = httpx.Client(base_url=attestor_url, timeout=120.0)
        self.chain = Chain(cfg)
        self.cfg = cfg
        self.reports_dir = reports_dir
        self.run_id = run_id()
        self.batch_a: dict | None = None  # AttestResult of scenario (a)
        self.batch_b: dict | None = None  # AttestResult of scenario (b), rejected by the verifier
        self.report_a: dict | None = None

    # ------------------------------------------------------------------ helpers
    def load_report(self, name: str) -> dict:
        return load_report(name, self.reports_dir)

    def free_period(self, report: dict, sc: Scenario) -> dict:
        """Shared logic (attestor/scenarios.py): nearest earlier month whose slot is empty or Rejected."""
        chosen = free_period(self.chain.batch_of, lambda bid: self.chain.batch(bid)["status"], report)
        if chosen["period"] != report["period"]:
            sc.notes.append(f"period {report['period']} already attested on this chain; using fresh period {chosen['period']}")
            print(f"    note: {sc.notes[-1]}")
        return chosen

    def sign_report(self, report: dict, who: str) -> str:
        return sign_report(report, KEYS[who])

    def attest(self, report: dict, who: str) -> tuple[httpx.Response, float]:
        t0 = time.perf_counter()
        r = self.http.post("/attest", json={"report": report, "signature": self.sign_report(report, who)})
        return r, time.perf_counter() - t0

    def disclosure(self, batch_id: int, who: str, ts: int | None = None) -> httpx.Response:
        msg = f"EmbedProof disclosure\nbatch:{batch_id}\nts:{ts if ts is not None else int(time.time())}"
        sig = "0x" + Account.sign_message(encode_defunct(text=msg), KEYS[who]).signature.hex()
        return self.http.post(f"/disclosure/{batch_id}", json={"address": ADDR[who], "message": msg, "signature": sig})

    def execution_gas(self, tx_hash: str, gas_used: int) -> int:
        """gasUsed minus 21,000 intrinsic and calldata gas: what a gasleft() delta around the call sees.
        This is the quantity the contract tests hold to the 250k / 120k budgets (INTERFACES section 3)."""
        data = bytes(self.chain.w3.eth.get_transaction(tx_hash)["input"])
        return gas_used - 21_000 - sum(4 if b == 0 else 16 for b in data)

    @staticmethod
    def attestation_fields(b: dict, commitment: bytes) -> dict:
        """EIP-712 Attestation struct as the contract sees it: on-chain batch fields plus the given commitment."""
        return {"installationId": bytes(b["installationId"]), "period": int(b["period"]), "commitment": commitment,
                "seeDeclared": int(b["seeDeclared"]), "verifiedTonnes": int(b["verifiedTonnes"]),
                "riskScore": int(b["riskScore"]), "modelVersionHash": bytes(b["modelVersionHash"]),
                "algId": int(b["algId"]), "pqSigHash": bytes(b["pqSigHash"])}

    def attest_input(self, b: dict, ecdsa_sig, **override) -> tuple:
        """AttestInput tuple (ABI component order) rebuilt from an on-chain batch, optionally with edited fields."""
        sig = bytes.fromhex(ecdsa_sig[2:]) if isinstance(ecdsa_sig, str) else ecdsa_sig
        values = {**self.attestation_fields(b, bytes(b["commitment"])), "cidDigest": bytes(b["cidDigest"]),
                  "ecdsaSig": sig, **override}
        comps = self.chain.attestation.get_function_by_name("attest").abi["inputs"][0]["components"]
        return tuple(values[c["name"]] for c in comps)

    def consignment_id(self, batch_id: int, tag: str) -> bytes:
        return consignment_id(batch_id, tag, self.run_id)

    def preflight(self) -> None:
        h = self.http.get("/health").json()
        print(f"attestor  : {self.http.base_url}  ok={h['ok']}  signer={h['attestor']}  pq={h['pqBackend']}  ipfs={h['ipfsMode']}")
        print(f"model     : {h['modelVersionHash']}")
        print(f"chain     : id {self.chain.chain_id} @ {self.cfg.rpc_url}  attestation={self.chain.attestation_address}")
        problems = []
        if not h["ok"]:
            problems.append("attestor reports ok=false (cannot reach the chain)")
        if not self.chain.is_model_approved(bytes.fromhex(h["modelVersionHash"][2:])):
            problems.append("attestor model hash is not approved on-chain (deploy with MODEL_VERSION_HASH)")
        for who, role in (("exporterA", "OPERATOR_ROLE"), ("exporterB", "OPERATOR_ROLE"), ("verifier", "VERIFIER_ROLE")):
            if not self.chain.has_role(role, ADDR[who]):
                problems.append(f"{who} {ADDR[who]} lacks {role} (deploy with DEMO=1)")
        if not self.chain.has_role("ATTESTOR_ROLE", h["attestor"]):
            problems.append(f"attestor {h['attestor']} lacks ATTESTOR_ROLE")
        anchored = self.chain.attestor_pq_key(h["attestor"])
        if anchored != bytes.fromhex(h["pqPublicKeyHash"][2:]):
            problems.append(f"registry.attestorPqKey({h['attestor']}) is {'0x' + anchored.hex() if anchored else 'unavailable'}, "
                            f"attestor serves {h['pqPublicKeyHash']} (deploy with ATTESTOR_PQ_KEY_HASH=$(python -m attestor.keys))")
        for inst, who in (("TR-EAF-0001", "exporterA"), ("TR-BOF-0002", "exporterB")):
            info = self.chain.installation(inst)
            if info is None or info["operator"] != ADDR[who]:
                problems.append(f"{inst} is not registered with operator {ADDR[who]} (deploy with DEMO=1)")
        if problems:
            raise SystemExit("preflight failed:\n  - " + "\n  - ".join(problems))

    # ------------------------------------------------------------------ scenario (a)
    def scenario_a(self) -> Scenario:
        sc = Scenario("(a) honest EAF report -> green -> Attested, both signatures verify")
        report = self.free_period(self.load_report("honest_eaf.json"), sc)
        r, dt = self.attest(report, "exporterA")
        if not sc.check("POST /attest accepted", r.status_code == 200, f"HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''}"):
            return sc
        res = r.json()
        self.batch_a, self.report_a = res, report
        sc.check("band green, status Attested", res["band"] == "green" and res["status"] == "Attested",
                 f"band={res['band']} riskScore={res['riskScore']} status={res['status']}")
        sc.check(f"report submit -> tx hash < {LATENCY_BUDGET_S:.0f} s", dt < LATENCY_BUDGET_S,
                 f"{dt * 1000:.0f} ms; server: {r.headers.get('server-timing', 'n/a')}")
        sc.check(f"attest() receipt gas <= {ATTEST_GAS_BUDGET}", res["gasUsed"] <= ATTEST_GAS_BUDGET,
                 f"receipt {res['gasUsed']} (execution {self.execution_gas(res['txHash'], res['gasUsed'])})")

        b = self.chain.batch(res["batchId"])
        sc.check("on-chain batch is Attested with matching commitment",
                 b["status"] == "Attested" and "0x" + bytes(b["commitment"]).hex() == res["commitment"],
                 f"batchId={res['batchId']} tx={res['txHash'][:18]}...")
        sc.check("on-chain attestor is the attestor service key", b["attestor"] == self.chain.attestor_address)

        # Independent verification here, with primitives only (does not call /verify).
        raw = self.http.get(f"/bundle/{res['cid']}").content
        digest = bundle.sha256(raw)
        sc.check("sha256(bundle) == CID digest == on-chain cidDigest",
                 digest == bundle.digest_from_cid(res["cid"]) == bytes(b["cidDigest"]))
        parsed = bundle.parse_bundle(raw)
        canonical = bundle.open_bundle(parsed, bundle.b64url_decode(res["keyB64url"]))
        commitment = keccak256(canonical)
        sc.check("keccak256(JCS(document)) == on-chain commitment", commitment == bytes(b["commitment"]))
        fields = self.attestation_fields(b, commitment)
        signer = recover_attestation_signer(fields, self.chain.chain_id, self.chain.attestation_address,
                                            parsed["ecdsaSignature"])
        sc.check("EIP-712 Attestation signature (typed data from on-chain fields + recomputed commitment) recovers "
                 "to the on-chain attestor", signer == b["attestor"], signer)
        local_digest = attestation_digest(fields, self.chain.chain_id, self.chain.attestation_address)
        try:
            onchain_digest = bytes(self.chain.attestation.functions.hashAttestation(
                self.attest_input(b, parsed["ecdsaSignature"])).call())
            sc.check("contract hashAttestation() == locally computed EIP-712 digest", onchain_digest == local_digest,
                     "0x" + local_digest.hex()[:18] + "...")
        except Exception as exc:  # ABI without hashAttestation, or a revert
            sc.check("contract hashAttestation() == locally computed EIP-712 digest", False, type(exc).__name__)
        backend = select_backend()
        sc.check(f"ML-DSA-44 signature verifies over the canonical bytes ({parsed['raw']['mldsa']['backend']} bundle, "
                 f"{backend.label} verifier)", backend.verify(parsed["mldsaPublicKey"], canonical, parsed["mldsaSignature"]))
        sc.check("keccak256(ML-DSA signature) == on-chain pqSigHash",
                 keccak256(parsed["mldsaSignature"]) == bytes(b["pqSigHash"]))
        pq_hash = keccak256(parsed["mldsaPublicKey"])
        sc.check("registry.isAttestorPqKey(attestor, keccak256(bundle ML-DSA public key))",
                 self.chain.is_attestor_pq_key(b["attestor"], pq_hash) is True, "0x" + pq_hash.hex()[:16] + "...")

        v = self.http.get(f"/verify/{res['cid']}").json()
        sc.check("GET /verify/{cid} allValid", v["allValid"], json.dumps({k: v[k] for k in (
            "commitmentMatches", "ecdsaValid", "mldsaValid", "pqSigHashMatches", "cidDigestMatches", "fieldsMatch",
            "pqKeyMatches")}))
        sc.check("GET /verify pqKeyMatches", v["pqKeyMatches"] is True)
        self.rescore(sc, f"http://localhost:3000{res['verifyPath']}")  # the certificate link the web UI shows
        return sc

    def rescore(self, sc: Scenario, certificate_url: str) -> None:
        """Run the real CLI (`python -m attestor.rescore <url>`) as a subprocess: it needs nothing but the chain, the
        pinned bundle, the key in the URL and the model artefact file."""
        cmd = [sys.executable, "-m", "attestor.rescore", certificate_url, "--attestor", str(self.http.base_url),
               "--rpc", self.cfg.rpc_url]
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "DEPLOYMENTS": str(self.cfg.deployments_dir)}
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=300)
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        for ln in lines:
            print(f"        rescore| {ln}")
        passes = [ln for ln in lines if ln.startswith("PASS")]
        sc.check("rescore CLI: (a) model artefact hash, (b) risk score, (c) commitment all PASS (exit 0)",
                 proc.returncode == 0 and len(passes) >= 3 and not any(ln.startswith("FAIL") for ln in lines),
                 f"exit {proc.returncode}, {len(passes)} PASS lines, {time.perf_counter() - t0:.1f} s"
                 + (f"; stderr: {proc.stderr.strip()[:200]}" if proc.returncode else ""))

    # ------------------------------------------------------------------ scenario (b)
    def scenario_b(self) -> Scenario:
        sc = Scenario("(b) implausible BF-BOF -> red -> PendingVerifier -> verifier rejects")
        report = self.free_period(self.load_report("implausible_bof.json"), sc)
        r, dt = self.attest(report, "exporterB")
        if not sc.check("POST /attest accepted", r.status_code == 200, f"HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''}"):
            return sc
        res = r.json()
        self.batch_b = res
        sc.check("band red, riskScore > 60, status PendingVerifier",
                 res["band"] == "red" and res["riskScore"] > 60 and res["status"] == "PendingVerifier",
                 f"band={res['band']} riskScore={res['riskScore']} status={res['status']}")
        top = res["score"]["shap"][0] if res["score"].get("shap") else None
        sc.check("SHAP drivers and explanation present", bool(top) and bool(res["score"].get("explanation")),
                 f"top driver: {top['label'] if top else None}; {res['score'].get('explanation', '')[:90]}")
        batch_id = res["batchId"]
        sc.check("on-chain status PendingVerifier", self.chain.batch(batch_id)["status"] == "PendingVerifier")

        text = (f"Declared SEE {report['seeTco2PerT']} t/t for a BF-BOF route is far below the plausible interval "
                f"(risk {res['riskScore']}); rejected by verifier. run {self.run_id}")
        rr = self.http.post("/reasons", json={"batchId": batch_id, "text": text})
        sc.check("POST /reasons stores the verifier's reason", rr.status_code == 200, f"HTTP {rr.status_code}")
        reason_hash = rr.json()["reasonHash"]
        sc.check("reasonHash == keccak256(utf8(text))", reason_hash == "0x" + keccak(text.encode()).hex())
        tx = self.chain.verifier_sign(batch_id, False, bytes.fromhex(reason_hash[2:]), KEYS["verifier"])
        b = self.chain.batch(batch_id)
        sc.check("verifierSign(false) -> Rejected, verifier and reasonHash recorded",
                 b["status"] == "Rejected" and b["verifier"] == ADDR["verifier"] and "0x" + bytes(b["reasonHash"]).hex() == reason_hash,
                 f"status={b['status']} tx={tx.tx_hash[:18]}...")
        sc.check("GET /reasons/{hash} returns the stored text", self.http.get(f"/reasons/{reason_hash}").json().get("text") == text)
        return sc

    # ------------------------------------------------------------------ scenario (c)
    def scenario_c(self) -> Scenario:
        sc = Scenario("(c) allocate 5,000 t to importer A, then the same 5,000 t to importer B -> DoubleClaimBlocked")
        if not self.batch_a:
            sc.error = "scenario (a) did not produce a batch"
            return sc
        batch_id, report = self.batch_a["batchId"], self.report_a
        al = self.chain.allocator
        verified = self.chain.batch(batch_id)["verifiedTonnes"]
        if not sc.check("batch (a) has 5,000 verified tonnes", verified == 5000, f"verifiedTonnes={verified}"):
            return sc
        cn = int(report["cnCode"])
        c_a, c_b = self.consignment_id(batch_id, "A"), self.consignment_id(batch_id, "B")

        tx = self.chain.allocate(batch_id, c_a, ADDR["importerA"], cn, 5000, KEYS["exporterA"])
        sc.check(f"exporter A allocate(5000 t -> importer A) succeeds, receipt gas <= {ALLOCATE_GAS_BUDGET}",
                 tx.gas_used <= ALLOCATE_GAS_BUDGET,
                 f"receipt {tx.gas_used} (execution {self.execution_gas(tx.tx_hash, tx.gas_used)})")
        sc.check("remainingTonnes == 0", al.functions.remainingTonnes(batch_id).call() == 0)
        self.chain.claim(c_a, KEYS["importerA"])
        alloc = al.functions.getAllocation(c_a).call()
        sc.check("importer A claimed the allocation", bool(alloc[4]) and alloc[1] == ADDR["importerA"] and alloc[3] == 5000)

        try:
            self.chain.allocate(batch_id, c_b, ADDR["importerB"], cn, 5000, KEYS["exporterA"])
            sc.check("second allocation of the same 5,000 t reverts", False, "transaction unexpectedly succeeded")
        except ChainRevert as exc:
            args = exc.revert_args
            sc.check("second allocation reverts DoubleClaimBlocked(batchId, 5000, 0)",
                     exc.name == "DoubleClaimBlocked" and args.get("batchId") == batch_id
                     and args.get("requested") == 5000 and args.get("remaining") == 0, str(exc))
        sc.check("allocated tonnes unchanged at 5,000", al.functions.allocatedTonnes(batch_id).call() == 5000)
        return sc

    # ------------------------------------------------------------------ scenario (d)
    def scenario_d(self) -> Scenario:
        sc = Scenario("(d) tamper test: one edited digit breaks commitment and both signatures")
        if not self.batch_a:
            sc.error = "scenario (a) did not produce a batch"
            return sc
        res = self.batch_a
        r = self.disclosure(res["batchId"], "exporterA")
        if not sc.check("operator obtains the document via /disclosure", r.status_code == 200, f"HTTP {r.status_code}"):
            return sc
        disc = r.json()
        sc.check("disclosed key matches the attest result", disc["keyB64url"] == res["keyB64url"])
        doc = disc["document"]

        ok = self.http.post("/verify", json={"cid": res["cid"], "document": doc}).json()
        sc.check("untampered document: commitment, ECDSA, ML-DSA, pqKeyMatches all true",
                 ok["commitmentMatches"] and ok["ecdsaValid"] and ok["mldsaValid"] and ok["pqKeyMatches"] and ok["allValid"],
                 f"allValid={ok['allValid']}")

        tampered = copy.deepcopy(doc)
        before = tampered["report"]["productionTonnes"]
        tampered["report"]["productionTonnes"] = before + 1  # one digit: ...0 -> ...1
        bad = self.http.post("/verify", json={"cid": res["cid"], "document": tampered}).json()
        sc.check(f"productionTonnes {before} -> {before + 1}: commitmentMatches false", bad["commitmentMatches"] is False,
                 f"recomputed {bad['commitmentRecomputed'][:14]}... vs on-chain {bad['commitmentOnChain'][:14]}...")
        sc.check("ecdsaValid false", bad["ecdsaValid"] is False, f"recovered {bad['ecdsaSigner']}")
        sc.check("mldsaValid false", bad["mldsaValid"] is False)
        sc.check("allValid false", bad["allValid"] is False)
        sc.notes.append("pqSigHashMatches / cidDigestMatches / pqKeyMatches stay true by design: they describe the pinned "
                        "bundle bytes and its ML-DSA key, which the document edit does not touch")
        return sc

    # ------------------------------------------------------------------ hardening probes
    def probes(self) -> Scenario:
        sc = Scenario("(e) hardening probes (extra)")
        if not self.batch_a:
            sc.error = "scenario (a) did not produce a batch"
            return sc
        rep, res = self.report_a, self.batch_a
        r, _ = self.attest(rep, "exporterB")
        sc.check("report signed by a non-operator -> 403", r.status_code == 403, r.json().get("detail", "")[:90])
        wrong_route = {**rep, "route": "BF-BOF"}
        r, _ = self.attest(wrong_route, "exporterA")
        sc.check("route differs from registry -> 422", r.status_code == 422, r.json().get("detail", "")[:90])
        r, _ = self.attest(rep, "exporterA")
        sc.check("re-submitting an attested period -> 409 DuplicatePeriod",
                 r.status_code == 409 and "DuplicatePeriod" in r.json().get("detail", ""), r.json().get("detail", "")[:110])
        sc.check("disclosure to importer A (holds an allocation) -> 200", self.disclosure(res["batchId"], "importerA").status_code == 200)
        sc.check("disclosure to importer B (no allocation) -> 403", self.disclosure(res["batchId"], "importerB").status_code == 403)
        sc.check("disclosure with a 10-minute-old timestamp -> 403",
                 self.disclosure(res["batchId"], "exporterA", ts=int(time.time()) - 600).status_code == 403)
        self.replay_probes(sc)
        v = self.http.get(f"/verify/{res['cid']}").json()
        sc.check("GET /verify still allValid after all of the above", v["allValid"])
        return sc

    def replay_probes(self, sc: Scenario) -> None:
        """A valid attestor signature must not be reusable for other numbers (INTERFACES 9.A).

        Batch (b) is Rejected, so its (installation, period) slot is open again: the attacker calls attest()
        directly with the signed record signature but a lowered riskScore, another period or other tonnes. The
        signature covers all of them, so the contract recovers a different signer: BadAttestorSignature."""
        if not self.batch_b:
            sc.check("replay probes need scenario (b)", False, "no batch (b)")
            return
        b = self.chain.batch(self.batch_b["batchId"])
        sig = bytes.fromhex(self.batch_b["ecdsaSignature"][2:])
        for label, change in ((f"riskScore {b['riskScore']} -> 5", {"riskScore": 5}),
                              ("period moved to 2000-01", {"period": 200001}),
                              ("verifiedTonnes x2", {"verifiedTonnes": int(b["verifiedTonnes"]) * 2})):
            before = self.chain.attestation.functions.batchCount().call()
            try:
                self.chain.send(self.chain.attestation.functions.attest(
                    self.attest_input(b, sig, **change), self.batch_b["cid"]), self.cfg.attestor_private_key)
                sc.check(f"replayed signature with {label} reverts", False, "transaction unexpectedly succeeded")
            except ChainRevert as exc:
                sc.check(f"replayed signature with {label} -> BadAttestorSignature",
                         exc.name == "BadAttestorSignature", str(exc)[:90])
            sc.check("no batch was created by the replay", self.chain.attestation.functions.batchCount().call() == before)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attestor", default=os.environ.get("ATTESTOR_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--rpc", default=None, help="Anvil RPC URL (default: RPC_URL env or http://127.0.0.1:8545)")
    ap.add_argument("--reports", default=str(REPO_ROOT / "data" / "demo_reports"))
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    if args.rpc:
        cfg = replace(cfg, rpc_url=args.rpc)
    try:
        demo = Demo(args.attestor, cfg, Path(args.reports))
        demo.preflight()
    except (httpx.HTTPError, ChainError, FileNotFoundError) as exc:
        print(f"cannot start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    results: list[Scenario] = []
    t0 = time.perf_counter()
    for fn in (demo.scenario_a, demo.scenario_b, demo.scenario_c, demo.scenario_d, demo.probes):
        print(f"\n{fn.__name__.replace('_', ' ')}:")
        try:
            sc = fn()
        except SystemExit:
            raise
        except Exception as exc:  # a crash is a failure of that scenario, not of the whole run
            sc = Scenario(fn.__name__)
            sc.error = f"{type(exc).__name__}: {exc}"
            print(f"    [FAIL] crashed: {sc.error}")
        results.append(sc)

    print("\n" + "=" * 100)
    print("SCORECARD")
    print("=" * 100)
    for sc in results:
        n_ok = sum(1 for _, ok, _ in sc.checks if ok)
        status = "PASS" if sc.passed else "FAIL"
        print(f"{status}  {sc.name}  [{n_ok}/{len(sc.checks)} checks]" + (f"  ERROR: {sc.error}" if sc.error else ""))
        for label, ok, detail in sc.checks:
            if not ok:
                print(f"        failed: {label}  {detail}")
        for note in sc.notes:
            print(f"        note: {note}")
    failed = [s for s in results if not s.passed]
    print("=" * 100)
    print(f"{len(results) - len(failed)}/{len(results)} scenarios passed in {time.perf_counter() - t0:.1f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
