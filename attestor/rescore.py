"""Independent re-score of a certificate:  python -m attestor.rescore "<certificate URL>"

    python -m attestor.rescore "http://localhost:3000/verify/3#k=<base64url key>" [--attestor URL] [--artefact PATH]

Given the certificate link (batch id plus the decryption key in the URL fragment) it reads the batch from the chain
(RPC_URL / deployments), fetches the pinned bundle by the CID derived from the on-chain cidDigest, decrypts it and re-runs the
frozen model on the recorded `assessment.inputs` and report. Checks (exit code 1 if any FAIL):

  (a) sha256(model artefact file) == on-chain modelVersionHash
  (b) recomputed risk score == on-chain riskScore
  (c) keccak256(JCS(document)) == on-chain commitment
  (d) sha256(bundle) == on-chain cidDigest                     (extra: the bytes are the ones the chain commits to)
  (e) recomputed interval and band == the signed assessment    (extra)

The scorer is imported from the repo's `model` package and loaded with a throwaway installations file built from the
recorded inputs, so nothing outside the certificate, the chain and the artefact file influences the result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from .bundle import BundleError, b64url_decode, cid_from_digest, open_bundle, parse_bundle, sha256
from .canonical import hex0x, keccak256
from .config import Config

_PATH_RE = re.compile(r".*/verify/([0-9]{1,18})/?")


class RescoreError(RuntimeError):
    """Usage or retrieval problem (exit code 2), as opposed to a failed check (exit code 1)."""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


def parse_certificate_url(url: str) -> tuple[int, bytes]:
    """('.../verify/<batchId>#k=<base64url key>') -> (batchId, 32-byte key)."""
    parts = urlsplit(url.strip())
    m = _PATH_RE.fullmatch(parts.path)
    if not m:
        raise RescoreError("URL path must end with /verify/<batchId>")
    keys = parse_qs(parts.fragment).get("k")
    if not keys:
        raise RescoreError("URL fragment must carry the decryption key: #k=<base64url key>")
    try:
        key = b64url_decode(keys[0], "k")
    except BundleError as exc:
        raise RescoreError(str(exc)) from exc
    if len(key) != 32:
        raise RescoreError("the key in #k= must decode to 32 bytes")
    return int(m.group(1)), key


def installations_from_inputs(inputs: dict) -> dict:
    """A one-entry installations file that makes the scorer use exactly the recorded feature values."""
    inst, f = inputs["installation"], inputs["features"]
    priors = {k: f[name] for k, name in (("capacityTpy", "capacityTpy"), ("gridGco2PerKwh", "gridGco2PerKwh"),
                                         ("ctLaggedPriorEf", "ctLaggedPriorEf")) if f.get(name) is not None}
    return {inst["id"]: {"country": inst.get("country"), "priors": priors}}


def rescore_document(document: dict, artefact_path: Path) -> dict:
    """Re-run model.scorer on the document's recorded inputs; returns a ScoreResult."""
    from model.scorer import Scorer  # the repo's model package (PYTHONPATH=. / installed layout)

    inputs = document["assessment"]["inputs"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "installations.json"
        path.write_text(json.dumps(installations_from_inputs(inputs)), encoding="utf-8")
        scorer = Scorer.load(artefact_path, path)
        return scorer.score_report(document["report"], dict(inputs["installation"]))


def run_checks(batch_id: int, key: bytes, chain, fetch_bundle: Callable[[str], bytes | None],
               artefact_path: Path) -> tuple[list[Check], dict]:
    """Returns (checks, context). Raises RescoreError when the data needed for the checks cannot be obtained."""
    batch = chain.batch(batch_id)
    if batch is None:
        raise RescoreError(f"batch {batch_id} does not exist on chain {chain.chain_id}")
    cid = cid_from_digest(bytes(batch["cidDigest"]))
    raw = fetch_bundle(cid)
    if raw is None:
        raise RescoreError(f"bundle {cid} could not be fetched")
    try:
        canonical = open_bundle(parse_bundle(raw), key)
        document = json.loads(canonical)
    except (BundleError, ValueError) as exc:
        raise RescoreError(f"cannot decrypt the bundle with the key in the URL: {exc}") from exc

    checks: list[Check] = []
    artefact_hash = "0x" + hashlib.sha256(Path(artefact_path).read_bytes()).hexdigest()
    onchain_model = hex0x(bytes(batch["modelVersionHash"]))
    checks.append(Check("(a) sha256(model artefact file) == on-chain modelVersionHash", artefact_hash == onchain_model,
                        artefact_hash if artefact_hash == onchain_model else f"artefact {artefact_hash} vs chain {onchain_model}"))

    try:
        score = rescore_document(document, Path(artefact_path))
        recomputed = int(score["riskScore"])
        checks.append(Check("(b) recomputed risk score == on-chain riskScore", recomputed == int(batch["riskScore"]),
                            f"recomputed {recomputed}, on-chain {int(batch['riskScore'])}"))
        signed = document["assessment"]
        same = all(score[k] == signed[k] for k in ("riskScore", "band", "q05", "q50", "q95"))
        checks.append(Check("(e) recomputed interval and band == the signed assessment", same,
                            f"{score['q05']}/{score['q50']}/{score['q95']} {score['band']}"))
    except KeyError as exc:
        checks.append(Check("(b) recomputed risk score == on-chain riskScore", False,
                            f"document has no assessment.inputs / report field {exc}"))
    except Exception as exc:  # scorer rejected the recorded inputs
        checks.append(Check("(b) recomputed risk score == on-chain riskScore", False, f"{type(exc).__name__}: {exc}"))

    commitment = keccak256(canonical)
    checks.append(Check("(c) keccak256(JCS(document)) == on-chain commitment", commitment == bytes(batch["commitment"]),
                        hex0x(commitment)))
    checks.append(Check("(d) sha256(bundle) == on-chain cidDigest", sha256(raw) == bytes(batch["cidDigest"]), cid))
    checks.sort(key=lambda c: c.label[:3])
    return checks, {"cid": cid, "batch": batch, "document": document}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m attestor.rescore", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="certificate URL: .../verify/<batchId>#k=<base64url key>")
    ap.add_argument("--attestor", default=os.environ.get("ATTESTOR_URL", "http://127.0.0.1:8000"),
                    help="attestor base URL used as the bundle gateway (GET /bundle/<cid>)")
    ap.add_argument("--artefact", default=None, help="model artefact file (default: the repo's frozen artefact)")
    ap.add_argument("--rpc", default=None, help="RPC URL (default: RPC_URL env)")
    args = ap.parse_args(argv)

    try:
        from dataclasses import replace

        from .chain import Chain, ChainError

        batch_id, key = parse_certificate_url(args.url)
        cfg = Config.from_env(read_only=True)
        if args.rpc:
            cfg = replace(cfg, rpc_url=args.rpc)
        from model.scorer import DEFAULT_ARTEFACT

        artefact = Path(args.artefact) if args.artefact else DEFAULT_ARTEFACT

        def fetch(cid: str) -> bytes | None:
            r = httpx.get(f"{args.attestor.rstrip('/')}/bundle/{cid}", timeout=30)
            return r.content if r.status_code == 200 else None

        checks, ctx = run_checks(batch_id, key, Chain(cfg), fetch, artefact)
    except (RescoreError, ChainError, httpx.HTTPError, FileNotFoundError, ImportError) as exc:
        print(f"rescore: cannot run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"rescore batch {batch_id}  cid {ctx['cid']}  chain {cfg.chain_id}")
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.label}" + (f"  [{c.detail}]" if c.detail else ""))
    failed = [c for c in checks if not c.ok]
    print("RESCORE " + ("PASS" if not failed else f"FAIL ({len(failed)} of {len(checks)} checks)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
