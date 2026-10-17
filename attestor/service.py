"""Attestor business logic: /score, /attest, /verify, /disclosure, /reasons (no HTTP types)."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from eth_utils import to_checksum_address

from . import disclosure
from .bundle import BundleError, b64url, b64url_decode, build_bundle, digest_from_cid, new_key, sha256
from .canonical import CanonicalizationError, commitment_of, from_hex, hex0x, jcs, keccak256, to_jsonable
from .chain import Chain, ChainError, TxPending
from .ipfs import IpfsStore
from .report import (ReportError, installation_id_bytes32, period_to_int, see_to_chain, tonnes_to_chain,
                     validate_report)
from .signing import MLDSASigner, SignatureError, recover_commitment_signer, sign_commitment
from .store import Store
from .verify import verify_bundle

log = logging.getLogger("attestor.service")

ATTESTATION_SCHEMA = "embedproof.attestation.v1"
ALG_ID = 1
MAX_REASON_BYTES = 4000
_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class ServiceError(Exception):
    """Carries an HTTP status for the API layer; `extra` is merged into the JSON error body."""

    def __init__(self, status: int, detail: str, extra: dict | None = None):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra or {}


class Timings:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.stages: list[tuple[str, float]] = []

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages.append((name, (time.perf_counter() - start) * 1000))

    def total_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def header(self) -> str:
        parts = [f"{n};dur={d:.1f}" for n, d in self.stages] + [f"total;dur={self.total_ms():.1f}"]
        return ", ".join(parts)


class Attestor:
    def __init__(self, *, chain: Chain, scorer: Any, store: Store, ipfs: IpfsStore, mldsa: MLDSASigner,
                 attestor_key: str, clock=time.time, monotonic=time.monotonic):
        self.chain, self.scorer, self.store, self.ipfs, self.mldsa = chain, scorer, store, ipfs, mldsa
        self._key = attestor_key
        self._clock = clock
        self._monotonic = monotonic
        self._anchored_until = 0.0  # monotonic deadline of the cached *positive* anchor check

    # ------------------------------------------------------------------ PQ key anchor (fail closed)
    PQ_ANCHOR_TTL = 30.0  # seconds a positive result is trusted; a mismatch is never cached

    def pq_anchor(self) -> tuple[bool, str]:
        """(anchored, detail): does registry.attestorPqKey(attestor) equal keccak256 of our ML-DSA public key?

        Only a positive result is cached (PQ_ANCHOR_TTL); a mismatch is re-read from the chain on every call, so
        the service recovers as soon as the admin runs setAttestorPqKey, and an anchor changed later is noticed."""
        own = keccak256(self.mldsa.public_key)
        if self._monotonic() < self._anchored_until:
            return True, ""
        try:
            registry = self.chain.attestor_pq_key(self.chain.attestor_address)
        except ChainError as exc:
            return False, (f"attestor ML-DSA key not anchored on-chain: cannot read registry.attestorPqKey ({exc}); "
                           f"attestor has {hex0x(own)}")
        if registry is None:
            shown = "no attestorPqKey function (registry deployed without PQ-key support)"
        else:
            shown = hex0x(registry) + (" (unset)" if registry == bytes(32) else "")
        if registry is not None and registry != bytes(32) and registry == own:
            self._anchored_until = self._monotonic() + self.PQ_ANCHOR_TTL
            return True, ""
        self._anchored_until = 0.0
        return False, (f"attestor ML-DSA key not anchored on-chain: registry has {shown}, attestor has {hex0x(own)}"
                       f" - run setAttestorPqKey({self.chain.attestor_address}, {hex0x(own)})")

    # ------------------------------------------------------------------ helpers
    def _installation_for(self, report: Any) -> dict | None:
        iid = report.get("installationId") if isinstance(report, dict) else None
        if not (isinstance(iid, str) and iid.isascii() and 1 <= len(iid) <= 32):
            return None  # validate_report will report the malformed id
        return self.chain.installation(iid)

    def _validated(self, report: Any) -> dict:
        installation = self._installation_for(report)
        try:
            validate_report(report, installation)
        except ReportError as exc:
            raise ServiceError(422, str(exc), {"errors": exc.errors}) from exc
        assert installation is not None
        return installation

    @staticmethod
    def _scorer_installation(installation: dict) -> dict:
        return {k: installation[k] for k in ("id", "route", "capacityTpy", "country")}

    def _score(self, report: dict, installation: dict) -> dict:
        try:
            result = self.scorer.score_report(report, self._scorer_installation(installation))
        except ValueError as exc:  # the scorer rejected the input (e.g. non-positive SEE)
            raise ServiceError(422, f"scorer rejected the report: {exc}") from exc
        except Exception as exc:
            log.exception("scorer failed")
            raise ServiceError(500, f"scorer failed: {type(exc).__name__}: {exc}") from exc
        try:
            return to_jsonable(result)  # numpy scalars -> plain JSON types, NaN/inf rejected
        except CanonicalizationError as exc:
            raise ServiceError(500, f"scorer returned an invalid result: {exc}") from exc

    # ------------------------------------------------------------------ endpoints
    def health(self) -> dict:
        connected = self.chain.connected()
        return {
            "ok": connected,
            "chainId": self.chain.chain_id,
            "attestor": self.chain.attestor_address,
            "modelVersionHash": self.scorer.model_version_hash,
            "pqBackend": self.mldsa.backend,
            "ipfsMode": self.ipfs.mode,
            "pqPublicKeyHash": hex0x(keccak256(self.mldsa.public_key)),
            "pqKeyAnchored": self.pq_anchor()[0],
        }

    def score(self, report: Any) -> dict:
        installation = self._validated(report)
        return self._score(report, installation)

    def attest(self, report: Any, signature: str, timings: Timings | None = None) -> dict:
        tm = timings or Timings()
        anchored, detail = self.pq_anchor()
        if not anchored:  # fail closed: never mint a record whose PQ signature cannot be tied to the chain
            raise ServiceError(503, detail)
        with tm.stage("validate"):
            installation = self._validated(report)
            try:
                digest = keccak256(jcs(report))
            except CanonicalizationError as exc:
                raise ServiceError(422, f"report cannot be canonicalised: {exc}") from exc
            try:
                exporter = recover_commitment_signer(digest, signature)
            except SignatureError as exc:
                raise ServiceError(422, f"exporter signature: {exc}") from exc
            if exporter != installation["operator"]:
                raise ServiceError(
                    403, f"report signed by {exporter}, but the registered operator of "
                         f"{installation['id']} is {installation['operator']}")
            installation_id, period = report["installationId"], period_to_int(report["period"])
            existing = self.chain.batch_of(installation_id, period)
            if existing:
                prev = self.chain.batch(existing)
                if prev and prev["status"] != "Rejected":
                    raise ServiceError(
                        409, f"DuplicatePeriod(installationId={installation_id}, period={period}, "
                             f"existingBatchId={existing}) [status {prev['status']}]",
                        {"revert": {"name": "DuplicatePeriod", "existingBatchId": existing}})

        with tm.stage("score"):
            score = self._score(report, installation)
        model_hash = from_hex(score["modelVersionHash"], 32)
        if not self.chain.is_model_approved(model_hash):
            raise ServiceError(503, f"model {score['modelVersionHash']} is not approved on-chain "
                                    "(admin must call approveModel)")

        with tm.stage("build"):
            document = {
                "schema": ATTESTATION_SCHEMA,
                "report": report,
                "assessment": {k: v for k, v in score.items() if k != "latencyMs"},
                "chainId": self.chain.chain_id,
                "attestationContract": to_checksum_address(self.chain.attestation_address),
                "salt": hex0x(os.urandom(32)),
                "issuedAt": datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            canonical = jcs(document)
            commitment = commitment_of(canonical)

        with tm.stage("sign"):
            ecdsa_sig, ecdsa_signer = sign_commitment(commitment, self._key)
            mldsa_sig = self.mldsa.sign(canonical)

        with tm.stage("bundle+ipfs"):
            key = new_key()
            bundle = build_bundle(
                chain_id=self.chain.chain_id, installation_id=installation_id, period=period,
                commitment=commitment, ecdsa_signer=ecdsa_signer, ecdsa_signature=ecdsa_sig,
                mldsa_backend=self.mldsa.backend, mldsa_public_key=self.mldsa.public_key,
                mldsa_signature=mldsa_sig, canonical=canonical, key=key)
            cid = self.ipfs.put(bundle)  # raises IpfsError if Pinata disagrees on the CID
            # Persist key + document before broadcasting: an on-chain record must never be undecryptable.
            self.store.put_pending(cid, b64url(key), canonical)

        a = {
            "installationId": installation_id_bytes32(installation_id),
            "period": period,
            "commitment": commitment,
            "seeDeclared": see_to_chain(report["seeTco2PerT"]),
            "verifiedTonnes": tonnes_to_chain(report["productionTonnes"]),
            "riskScore": int(score["riskScore"]),
            "modelVersionHash": model_hash,
            "algId": ALG_ID,
            "pqSigHash": keccak256(mldsa_sig),
            "cidDigest": sha256(bundle),
            "ecdsaSig": bytes.fromhex(ecdsa_sig[2:]),
        }
        try:
            with tm.stage("tx"):
                tx, ev = self.chain.attest(a, cid)
        except TxPending:
            raise  # may still be mined: keep the key so the record stays decryptable
        except Exception:
            self.store.discard_pending(cid)
            raise

        batch_id = int(ev["batchId"])
        self.store.put_batch(
            batch_id=batch_id, cid=cid, installation_id=installation_id, period=period,
            commitment=hex0x(commitment), tx_hash=tx.tx_hash, status=ev["status"],
            risk_score=int(score["riskScore"]), exporter=exporter, exporter_sig=signature)
        key_b64url = b64url(key)
        log.info("attested batch=%s %s %s status=%s risk=%s gas=%s timings: %s", batch_id, installation_id,
                 report["period"], ev["status"], score["riskScore"], tx.gas_used, tm.header())
        return {
            "batchId": batch_id,
            "txHash": tx.tx_hash,
            "status": ev["status"],
            "riskScore": int(score["riskScore"]),
            "band": score["band"],
            "cid": cid,
            "commitment": hex0x(commitment),
            "pqSigHash": hex0x(a["pqSigHash"]),
            "ecdsaSignature": ecdsa_sig,
            "keyB64url": key_b64url,
            "verifyPath": f"/verify/{batch_id}#k={key_b64url}",
            "score": score,
            "gasUsed": tx.gas_used,
        }

    def verify(self, cid: str, document: Any | None = None) -> dict:
        try:
            digest_from_cid(cid)
        except BundleError as exc:
            raise ServiceError(422, f"invalid cid: {exc}") from exc
        try:
            bundle = self.ipfs.get(cid)
        except BundleError as exc:
            raise ServiceError(502, str(exc)) from exc
        if bundle is None:
            raise ServiceError(404, f"bundle {cid} not found")

        rec = self.store.batch_by_cid(cid)
        batch_id = rec["batch_id"] if rec else self.chain.find_batch_by_cid(cid)
        batch = self.chain.batch(batch_id) if batch_id else None
        if batch is None:
            raise ServiceError(404, f"no on-chain attestation references {cid}")

        key = None
        if document is None:
            stored = self.store.get_key(cid)
            if stored is None:
                raise ServiceError(404, "this attestor does not hold the decryption key for that CID; "
                                        "POST /verify with the decrypted document instead")
            key = b64url_decode(stored)
        else:
            if not isinstance(document, dict):
                raise ServiceError(422, "document must be a JSON object")
        try:
            return verify_bundle(cid=cid, bundle_bytes=bundle, batch=batch, mldsa=self.mldsa,
                                 chain_id=self.chain.chain_id, attestation_address=self.chain.attestation_address,
                                 pq_key_on_chain=self.chain.attestor_pq_key(batch["attestor"]),
                                 key=key, document=document)
        except CanonicalizationError as exc:
            raise ServiceError(422, f"document cannot be canonicalised: {exc}") from exc
        except BundleError as exc:
            raise ServiceError(422, f"bundle: {exc}") from exc

    def disclosure(self, batch_id: int, address: str, message: str, signature: str) -> dict:
        try:
            signer = disclosure.authenticate(batch_id, address, message, signature, self._clock())
        except disclosure.DisclosureAuthError as exc:
            raise ServiceError(exc.status, exc.detail) from exc
        batch = self.chain.batch(batch_id)
        if batch is None:
            raise ServiceError(404, f"unknown batch {batch_id}")
        rec = self.store.get_batch(batch_id)
        if rec is None:
            raise ServiceError(404, f"batch {batch_id} was not attested by this service")

        authorised = (
            self.chain.has_role("VERIFIER_ROLE", signer)
            or self.chain.has_role("REGULATOR_ROLE", signer)
            or self.chain.operator_of(batch["installationIdStr"]) == signer
            or signer in self.chain.importers_of(batch_id)
        )
        if not authorised:
            raise ServiceError(403, "signer is not a verifier, regulator, the installation operator, "
                                    "or an importer with an allocation on this batch")
        canonical, key = self.store.get_document(rec["cid"]), self.store.get_key(rec["cid"])
        if canonical is None or key is None:
            raise ServiceError(404, "document or key missing from the attestor store")
        return {"keyB64url": key, "document": json.loads(canonical)}

    def post_reason(self, batch_id: int, text: str) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ServiceError(422, "text must be a non-empty string")
        raw = text.encode("utf-8")
        if len(raw) > MAX_REASON_BYTES:
            raise ServiceError(422, f"text exceeds {MAX_REASON_BYTES} bytes")
        if self.chain.batch(batch_id) is None:
            raise ServiceError(404, f"unknown batch {batch_id}")
        reason_hash = hex0x(keccak256(raw))
        self.store.put_reason(reason_hash, batch_id, text)
        return {"reasonHash": reason_hash}

    def get_reason(self, reason_hash: str) -> dict:
        if not _HASH_RE.match(reason_hash):
            raise ServiceError(422, "reasonHash must be 0x + 64 hex chars")
        text = self.store.get_reason(reason_hash.lower())
        if text is None:
            raise ServiceError(404, "reason not found")
        return {"text": text}

