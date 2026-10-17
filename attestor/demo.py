"""Headless runner for the four PRD demo scenarios (PRD section 13), against a live Anvil + attestor.

    PYTHONPATH=. python -m attestor.demo [--attestor http://127.0.0.1:8000] [--rpc http://127.0.0.1:8545]

(a) honest EAF report -> green -> Attested, both signatures verify
(b) implausible BF-BOF report -> red -> PendingVerifier -> verifier rejects with a stored reason
(c) exporter A allocates 5,000 t to importer A (claimed), then the same 5,000 t to importer B
    -> reverts DoubleClaimBlocked(batchId, 5000, 0)
(d) tamper test: one edited digit flips commitment / ECDSA / ML-DSA checks; the untampered document passes
Then a few hardening probes. Exit code is non-zero if any check fails. Safe to re-run on the same chain:
periods that are already used are advanced to the next free month and consignment ids are fresh.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from . import bundle
from .canonical import jcs, keccak256
from .chain import Chain, ChainError, ChainRevert
from .config import REPO_ROOT, Config
from .report import period_to_int
from .signing import recover_commitment_signer, select_backend, sign_commitment

# Public Anvil test keys #2..#6 (docs/INTERFACES.md section 1). Never use them on a real network.
KEYS = {
    "exporterA": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "exporterB": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "verifier": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "importerA": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    "importerB": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
}
ADDR = {k: Account.from_key(v).address for k, v in KEYS.items()}
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
        self.run_id = os.urandom(3).hex()
        self.batch_a: dict | None = None  # AttestResult of scenario (a)
        self.report_a: dict | None = None

    # ------------------------------------------------------------------ helpers
    def load_report(self, name: str) -> dict:
        return json.loads((self.reports_dir / name).read_text(encoding="utf-8"))

    def free_period(self, report: dict, sc: Scenario) -> dict:
        """First period >= the report's whose slot is empty or Rejected (the only re-attestable state)."""
        report = copy.deepcopy(report)
        year, month = int(report["period"][:4]), int(report["period"][5:7])
        original = report["period"]
        for _ in range(60):
            report["period"] = f"{year:04d}-{month:02d}"
            existing = self.chain.batch_of(report["installationId"], period_to_int(report["period"]))
            if existing == 0 or self.chain.batch(existing)["status"] == "Rejected":
                break
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        if report["period"] != original:
            sc.notes.append(f"period {original} already attested on this chain; using fresh period {report['period']}")
            print(f"    note: {sc.notes[-1]}")
        return report

    def sign_report(self, report: dict, who: str) -> str:
        return sign_commitment(keccak256(jcs(report)), KEYS[who])[0]

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

    def consignment_id(self, batch_id: int, tag: str) -> bytes:
        return f"EP-{batch_id}-{tag}-{self.run_id}".encode().ljust(32, b"\x00")

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
        signer = recover_commitment_signer(commitment, parsed["ecdsaSignature"])
        sc.check("ECDSA signature recovers to the on-chain attestor", signer == b["attestor"], signer)
        backend = select_backend()
        sc.check(f"ML-DSA-44 signature verifies over the canonical bytes ({parsed['raw']['mldsa']['backend']} bundle, "
                 f"{backend.label} verifier)", backend.verify(parsed["mldsaPublicKey"], canonical, parsed["mldsaSignature"]))
        sc.check("keccak256(ML-DSA signature) == on-chain pqSigHash",
                 keccak256(parsed["mldsaSignature"]) == bytes(b["pqSigHash"]))
        anchored = self.chain.attestor_pq_key(b["attestor"])
        sc.check("keccak256(bundle ML-DSA public key) == registry.attestorPqKey(attestor), non-zero",
                 bool(anchored) and anchored != bytes(32) and keccak256(parsed["mldsaPublicKey"]) == anchored,
                 "0x" + (anchored or b"").hex()[:16] + "...")

        v = self.http.get(f"/verify/{res['cid']}").json()
        sc.check("GET /verify/{cid} allValid", v["allValid"], json.dumps({k: v[k] for k in (
            "commitmentMatches", "ecdsaValid", "mldsaValid", "pqSigHashMatches", "cidDigestMatches", "fieldsMatch",
            "pqKeyMatches")}))
        sc.check("GET /verify pqKeyMatches", v["pqKeyMatches"] is True)
        return sc

    # ------------------------------------------------------------------ scenario (b)
    def scenario_b(self) -> Scenario:
        sc = Scenario("(b) implausible BF-BOF -> red -> PendingVerifier -> verifier rejects")
        report = self.free_period(self.load_report("implausible_bof.json"), sc)
        r, dt = self.attest(report, "exporterB")
        if not sc.check("POST /attest accepted", r.status_code == 200, f"HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''}"):
            return sc
        res = r.json()
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
        tx = self.chain.send(self.chain.attestation.functions.verifierSign(batch_id, False, bytes.fromhex(reason_hash[2:])),
                             KEYS["verifier"])
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

        tx = self.chain.send(al.functions.allocate(batch_id, c_a, ADDR["importerA"], cn, 5000), KEYS["exporterA"])
        sc.check(f"exporter A allocate(5000 t -> importer A) succeeds, receipt gas <= {ALLOCATE_GAS_BUDGET}",
                 tx.gas_used <= ALLOCATE_GAS_BUDGET,
                 f"receipt {tx.gas_used} (execution {self.execution_gas(tx.tx_hash, tx.gas_used)})")
        sc.check("remainingTonnes == 0", al.functions.remainingTonnes(batch_id).call() == 0)
        self.chain.send(al.functions.claim(c_a), KEYS["importerA"])
        alloc = al.functions.getAllocation(c_a).call()
        sc.check("importer A claimed the allocation", bool(alloc[4]) and alloc[1] == ADDR["importerA"] and alloc[3] == 5000)

        try:
            self.chain.send(al.functions.allocate(batch_id, c_b, ADDR["importerB"], cn, 5000), KEYS["exporterA"])
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
        v = self.http.get(f"/verify/{res['cid']}").json()
        sc.check("GET /verify still allValid after all of the above", v["allValid"])
        return sc


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
