"""Independent re-verification of a bundle against the on-chain Batch (FR-A7, INTERFACES sections 6 and 9)."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .bundle import digest_from_cid, open_bundle, parse_bundle, sha256
from .canonical import commitment_of, hex0x, jcs, keccak256
from .report import (ID_RE, PERIOD_RE, installation_id_bytes32, period_to_int, see_to_chain, tonnes_to_chain)
from .signing import MLDSASigner, SignatureError, recover_attestation_signer

_HASH32_RE = re.compile(r"0x[0-9a-f]{64}")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def _is_int(v: Any) -> bool:
    """A JSON integer. bool is an int subclass in Python and must not pass; floats (4.9) and strings ("4") neither."""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _doc_mismatches(doc_bytes: bytes, batch: dict, chain_id: int, attestation_address: str) -> list[str]:
    """Names of the fields where the committed document disagrees with the on-chain batch.

    Strictly typed: ints are ints (no bool, no 4.9, no "4"), period must fullmatch YYYY-MM, hashes are
    lower-case 0x + 64 hex. Anything structurally wrong is a mismatch, never an exception."""
    try:
        doc = json.loads(doc_bytes.decode("utf-8"))
        rep, assess = doc["report"], doc["assessment"]
    except Exception as exc:
        return [f"document structure: {type(exc).__name__}"]
    if not isinstance(doc, dict) or not isinstance(rep, dict) or not isinstance(assess, dict):
        return ["document structure: not objects"]

    bad: list[str] = []

    def check(name: str, ok_type: bool, compute: Callable[[], tuple[Any, Any]]) -> None:
        try:
            if not ok_type:
                bad.append(name)
                return
            got, want = compute()
            if got != want:
                bad.append(name)
        except Exception:
            bad.append(name)

    iid, period, see = rep.get("installationId"), rep.get("period"), rep.get("seeTco2PerT")
    prod = rep.get("productionTonnes")
    risk, mhash = assess.get("riskScore"), assess.get("modelVersionHash")
    contract = doc.get("attestationContract")

    check("chainId", _is_int(doc.get("chainId")), lambda: (doc["chainId"], chain_id))
    check("attestationContract", isinstance(contract, str) and bool(_ADDR_RE.fullmatch(contract)),
          lambda: (contract.lower(), attestation_address.lower()))
    check("installationId", isinstance(iid, str) and bool(ID_RE.fullmatch(iid)),
          lambda: (installation_id_bytes32(iid), bytes(batch["installationId"])))
    check("period", isinstance(period, str) and bool(PERIOD_RE.fullmatch(period)),
          lambda: (period_to_int(period), int(batch["period"])))
    check("seeDeclared", _is_number(see), lambda: (see_to_chain(see), int(batch["seeDeclared"])))
    check("verifiedTonnes", _is_number(prod), lambda: (tonnes_to_chain(prod), int(batch["verifiedTonnes"])))
    check("riskScore", _is_int(risk), lambda: (risk, int(batch["riskScore"])))
    check("modelVersionHash", isinstance(mhash, str) and bool(_HASH32_RE.fullmatch(mhash)),
          lambda: (mhash, hex0x(bytes(batch["modelVersionHash"]))))
    return bad


def _attestation_fields(batch: dict, commitment: bytes) -> dict:
    """The EIP-712 Attestation struct rebuilt from the on-chain batch plus the (recomputed) commitment."""
    return {
        "installationId": bytes(batch["installationId"]), "period": int(batch["period"]), "commitment": commitment,
        "seeDeclared": int(batch["seeDeclared"]), "verifiedTonnes": int(batch["verifiedTonnes"]),
        "riskScore": int(batch["riskScore"]), "modelVersionHash": bytes(batch["modelVersionHash"]),
        "algId": int(batch["algId"]), "pqSigHash": bytes(batch["pqSigHash"]),
    }


def verify_bundle(
    *,
    cid: str,
    bundle_bytes: bytes,
    batch: dict[str, Any],
    mldsa: MLDSASigner,
    chain_id: int,
    attestation_address: str,
    is_pq_key_anchored: Callable[[bytes], bool | None] | None = None,
    key: bytes | None = None,
    document: Any | None = None,
) -> dict[str, Any]:
    """Return a VerifyResult. Supply `document` (maybe tampered) or the AES `key`.

    * commitment is recomputed as keccak256(JCS(document)) and compared with the on-chain one;
    * ecdsaValid: the EIP-712 Attestation typed data is rebuilt from the on-chain batch fields with the
      RECOMPUTED commitment and the bundle's signature must recover to the on-chain attestor, so editing the
      document, or replaying the signature onto other numbers, moves the recovered address;
    * ML-DSA-44 is verified over the canonical bytes of the same document;
    * keccak(ML-DSA signature) must equal pqSigHash and sha256(bundle) must equal cidDigest;
    * pqKeyMatches: registry.isAttestorPqKey(batch.attestor, keccak256(bundle ML-DSA pk)) (key history).
    """
    parsed = parse_bundle(bundle_bytes)
    if document is not None:
        canonical = jcs(document)
    elif key is not None:
        canonical = open_bundle(parsed, key)
    else:
        raise ValueError("either key or document is required")

    recomputed = commitment_of(canonical)
    on_chain = bytes(batch["commitment"])

    signer: str | None
    try:
        signer = recover_attestation_signer(_attestation_fields(batch, recomputed), chain_id, attestation_address,
                                            parsed["ecdsaSignature"])
    except SignatureError:
        signer = None
    ecdsa_valid = signer is not None and signer.lower() == str(batch["attestor"]).lower()

    mldsa_valid = mldsa.verify(parsed["mldsaPublicKey"], canonical, parsed["mldsaSignature"])
    pq_sig_hash_matches = keccak256(parsed["mldsaSignature"]) == bytes(batch["pqSigHash"])
    cid_digest_matches = sha256(bundle_bytes) == bytes(batch["cidDigest"]) == digest_from_cid(cid)
    pq_key_matches = bool(is_pq_key_anchored and is_pq_key_anchored(keccak256(parsed["mldsaPublicKey"])) is True)

    commitment_matches = recomputed == on_chain
    mismatches = _doc_mismatches(canonical, batch, chain_id, attestation_address)
    return {
        "cid": cid,
        "batchId": int(batch["batchId"]),
        "commitmentOnChain": hex0x(on_chain),
        "commitmentRecomputed": hex0x(recomputed),
        "commitmentMatches": commitment_matches,
        "ecdsaValid": ecdsa_valid,
        "ecdsaSigner": signer,
        "mldsaValid": mldsa_valid,
        "pqSigHashMatches": pq_sig_hash_matches,
        "cidDigestMatches": cid_digest_matches,
        "fieldsMatch": not mismatches,
        "fieldMismatches": mismatches,  # additive: names the fields that disagree
        "pqKeyMatches": pq_key_matches,
        "allValid": all([commitment_matches, ecdsa_valid, mldsa_valid, pq_sig_hash_matches,
                         cid_digest_matches, not mismatches, pq_key_matches]),
    }
