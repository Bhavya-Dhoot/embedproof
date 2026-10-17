"""Independent re-verification of a bundle against the on-chain Batch (FR-A7)."""
from __future__ import annotations

import json
from typing import Any

from .bundle import open_bundle, parse_bundle, sha256, digest_from_cid
from .canonical import commitment_of, hex0x, jcs, keccak256
from .report import installation_id_bytes32, period_to_int, see_to_chain, tonnes_to_chain
from .signing import MLDSASigner, SignatureError, recover_commitment_signer


def _doc_mismatches(doc_bytes: bytes, batch: dict, chain_id: int, attestation_address: str) -> list[str]:
    """Compare the committed document with the numbers the attestor wrote on-chain."""
    try:
        doc = json.loads(doc_bytes.decode("utf-8"))
        rep, assess = doc["report"], doc["assessment"]
        expected = {
            "chainId": (doc["chainId"], chain_id),
            "attestationContract": (str(doc["attestationContract"]).lower(), attestation_address.lower()),
            "installationId": (installation_id_bytes32(rep["installationId"]), bytes(batch["installationId"])),
            "period": (period_to_int(rep["period"]), int(batch["period"])),
            "seeDeclared": (see_to_chain(rep["seeTco2PerT"]), int(batch["seeDeclared"])),
            "verifiedTonnes": (tonnes_to_chain(rep["productionTonnes"]), int(batch["verifiedTonnes"])),
            "riskScore": (int(assess["riskScore"]), int(batch["riskScore"])),
            "modelVersionHash": (str(assess["modelVersionHash"]).lower(), hex0x(bytes(batch["modelVersionHash"]))),
        }
    except Exception as exc:  # a tampered / foreign document may be structurally wrong
        return [f"document structure: {type(exc).__name__}"]
    return [k for k, (got, want) in expected.items() if got != want]


def verify_bundle(
    *,
    cid: str,
    bundle_bytes: bytes,
    batch: dict[str, Any],
    mldsa: MLDSASigner,
    chain_id: int,
    attestation_address: str,
    pq_key_on_chain: bytes | None = None,
    key: bytes | None = None,
    document: Any | None = None,
) -> dict[str, Any]:
    """Return a VerifyResult. Supply `document` (maybe tampered) or the AES `key`.

    * commitment is recomputed as keccak256(JCS(document)) and compared with the on-chain one;
    * the bundle's ECDSA signature is recovered over the *recomputed* commitment and must equal the
      on-chain attestor, so any edit moves the recovered address;
    * ML-DSA-44 is verified over the canonical bytes of the same document;
    * keccak(ML-DSA signature) must equal pqSigHash and sha256(bundle) must equal cidDigest;
    * keccak(bundle ML-DSA public key) must equal registry.attestorPqKey(batch.attestor) (`pq_key_on_chain`,
      non-zero), which anchors the PQ key to the attestor's on-chain identity.
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
        signer = recover_commitment_signer(recomputed, parsed["ecdsaSignature"])
    except SignatureError:
        signer = None
    ecdsa_valid = signer is not None and signer.lower() == str(batch["attestor"]).lower()

    mldsa_valid = mldsa.verify(parsed["mldsaPublicKey"], canonical, parsed["mldsaSignature"])
    pq_sig_hash_matches = keccak256(parsed["mldsaSignature"]) == bytes(batch["pqSigHash"])
    cid_digest_matches = sha256(bundle_bytes) == bytes(batch["cidDigest"]) == digest_from_cid(cid)

    pq_key_matches = (pq_key_on_chain is not None and pq_key_on_chain != bytes(32)
                      and keccak256(parsed["mldsaPublicKey"]) == pq_key_on_chain)

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
        # INTERFACES section 6: the committed document must agree with the on-chain numbers; fieldMismatches (additive) names the offenders
        "fieldsMatch": not mismatches,
        "fieldMismatches": mismatches,
        "pqKeyMatches": pq_key_matches,
        "allValid": all([commitment_matches, ecdsa_valid, mldsa_valid, pq_sig_hash_matches,
                         cid_digest_matches, not mismatches, pq_key_matches]),
    }
