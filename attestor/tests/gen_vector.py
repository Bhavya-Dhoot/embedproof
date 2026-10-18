"""Generate attestor/tests/vectors/bundle_vector.json (a full worked example for other implementations).

    PYTHONPATH=. python attestor/tests/gen_vector.py

Everything is produced by the production code paths (attestor.canonical/signing/bundle/report). Randomness is
pinned where the format allows it (salt, IV, AES key, issuedAt); the ML-DSA-44 key pair and signature are
fresh per run, which is fine because the file carries both. The `assessment` block is REAL output of the frozen model
(model/artefacts) for the INTERFACES section 4 example report, enriched by attestor/assess.py (hasHistory, flags, inputs),
so `python -m attestor.rescore` logic can reproduce its riskScore. Regenerate whenever the model artefact changes.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from attestor import bundle
from attestor.assess import Assessor
from attestor.canonical import commitment_of, hex0x, jcs, keccak256, to_jsonable
from attestor.report import installation_id_bytes32, period_to_int, see_to_chain, tonnes_to_chain
from attestor.signing import (ECDSA_SCHEME, MLDSASigner, attestation_digest, attestation_typed_data,
                              recover_attestation_signer, sign_attestation, sign_commitment)

ATTESTOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # Anvil #1 (public test key)
EXPORTER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # Anvil #2 (public test key)
ATTESTATION_CONTRACT = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
CHAIN_ID = 31337
OUT = Path(__file__).resolve().parent / "vectors" / "bundle_vector.json"

REPORT = {
    "schema": "embedproof.report.v1", "installationId": "TR-EAF-0001", "period": "2026-09", "route": "EAF",
    "cnCode": "72083900", "productionTonnes": 5000, "directEmissionsTco2": 1150.0, "electricityMwh": 2250.0,
    "gridFactorTco2PerMwh": 0.42, "indirectEmissionsTco2": 945.0, "seeTco2PerT": 0.419,
}
REPO = Path(__file__).resolve().parents[2]
INSTALLATION = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}  # as registered on-chain

# Same decimal-string rounding rule as the contract-facing code: round_half_up(Decimal(repr(x)) * 10^4).
SEE_VECTORS = ["0.419", "0.4195", "0.41955", "0.00005", "0.1", "1.00005", "2.12345", "0.29995", "123.45675", "1e-05", "5e-05",
               "0.5", "1.5e-4", "429496.7295"]


def fields_for(document: dict, commitment: bytes, pq_sig_hash: bytes) -> dict:
    """The EIP-712 Attestation struct exactly as attest() will see it on-chain."""
    rep, assess = document["report"], document["assessment"]
    return {
        "installationId": installation_id_bytes32(rep["installationId"]), "period": period_to_int(rep["period"]),
        "commitment": commitment, "seeDeclared": see_to_chain(rep["seeTco2PerT"]),
        "verifiedTonnes": tonnes_to_chain(rep["productionTonnes"]), "riskScore": assess["riskScore"],
        "modelVersionHash": bytes.fromhex(assess["modelVersionHash"][2:]), "algId": 1, "pqSigHash": pq_sig_hash,
    }


def real_assessment() -> dict:
    """Frozen-model ScoreResult for REPORT plus the attestor's hasHistory / flags / inputs (no latencyMs)."""
    from model.scorer import DEFAULT_ARTEFACT, Scorer

    installations = REPO / "data" / "installations_demo.json"
    scorer = Scorer.load(demo_installations_path=installations)
    score = to_jsonable(scorer.score_report(REPORT, INSTALLATION))
    assessor = Assessor(DEFAULT_ARTEFACT, installations)
    assert assessor.artefact_sha256 == scorer.model_version_hash
    enriched = assessor.enrich(REPORT, INSTALLATION, score)
    return {k: v for k, v in enriched.items() if k != "latencyMs"}


def main() -> None:
    assessment = real_assessment()
    document = {
        "schema": "embedproof.attestation.v1", "report": REPORT, "assessment": assessment, "chainId": CHAIN_ID,
        "attestationContract": ATTESTATION_CONTRACT, "salt": "0x" + bytes(range(32)).hex(),
        "issuedAt": "2026-10-18T12:00:00Z",
    }
    canonical = jcs(document)
    commitment = commitment_of(canonical)

    with tempfile.TemporaryDirectory() as tmp:
        mldsa = MLDSASigner.load_or_create(Path(tmp))
        mldsa_sig = mldsa.sign(canonical)
        assert mldsa.verify(mldsa.public_key, canonical, mldsa_sig)
        pq_sig_hash = keccak256(mldsa_sig)

        fields = fields_for(document, commitment, pq_sig_hash)
        ecdsa_sig, ecdsa_signer = sign_attestation(fields, CHAIN_ID, ATTESTATION_CONTRACT, ATTESTOR_KEY)
        assert recover_attestation_signer(fields, CHAIN_ID, ATTESTATION_CONTRACT, ecdsa_sig) == ecdsa_signer
        typed = attestation_typed_data(fields, CHAIN_ID, ATTESTATION_CONTRACT)

        key = bytes(range(100, 132))
        bundle_bytes = bundle.build_bundle(
            chain_id=CHAIN_ID, installation_id=REPORT["installationId"], period=202609, commitment=commitment,
            ecdsa_signer=ecdsa_signer, ecdsa_signature=ecdsa_sig, mldsa_backend=mldsa.backend,
            mldsa_public_key=mldsa.public_key, mldsa_signature=mldsa_sig, canonical=canonical, key=key,
            iv=bytes(range(12)))

        # A one-digit edit of the document: productionTonnes 5000 -> 5001.
        tampered = json.loads(canonical)
        tampered["report"]["productionTonnes"] = 5001
        tampered_canonical = jcs(tampered)
        tampered_commitment = commitment_of(tampered_canonical)
        # A verifier rebuilds the struct from the ON-CHAIN fields with the RECOMPUTED commitment.
        tampered_fields = {**fields, "commitment": tampered_commitment}
        tampered_signer = recover_attestation_signer(tampered_fields, CHAIN_ID, ATTESTATION_CONTRACT, ecdsa_sig)
        assert tampered_signer != ecdsa_signer

        report_digest = keccak256(jcs(REPORT))
        exporter_sig, exporter = sign_commitment(report_digest, EXPORTER_KEY)
        vector = {
            "_comment": [
                "EmbedProof bundle test vector (docs/INTERFACES.md sections 5 and 9).",
                "Keys are public Anvil test keys. The assessment block is real frozen-model output plus hasHistory / flags / inputs (round 3).",
                "To check in a browser: canonicalize(document) == canonical; keccak256(utf8(canonical)) == commitment;",
                "viem recoverTypedDataAddress({domain, types, primaryType:'Attestation', message, signature}) == ecdsa.signer,",
                "where `message` is eip712.message with commitment set to the commitment you RECOMPUTED (and the other",
                "fields taken from the on-chain batch); hashTypedData(...) == eip712.digest (cidDigest is not signed);",
                "@noble/post-quantum ml_dsa44.verify over utf8(canonical) with mldsa.publicKey/signature;",
                "AES-256-GCM decrypt bundle.encryption.ciphertext (ciphertext||tag) with key=base64url(aesKeyB64url),",
                "iv=base64(encryption.iv), AAD=the 32 commitment bytes -> utf8(canonical);",
                "pqKeyHash = keccak256(base64decode(mldsa.publicKey)) is what registry.isAttestorPqKey(signer, hash) holds.",
            ],
            "chainId": CHAIN_ID,
            "attestationContract": ATTESTATION_CONTRACT,
            "report": REPORT,
            "exporter": {"address": exporter, "reportDigest": hex0x(report_digest), "signature": exporter_sig,
                         "note": "EIP-191 personal_sign over the 32 raw bytes keccak256(JCS(report))"},
            "document": document,
            "canonical": canonical.decode("utf-8"),
            "commitment": hex0x(commitment),
            "ecdsa": {"scheme": ECDSA_SCHEME, "signer": ecdsa_signer, "signature": ecdsa_sig},
            "eip712": {
                "domain": typed["domain"],
                "primaryType": "Attestation",
                "types": {"Attestation": typed["types"]["Attestation"]},
                "message": typed["message"],
                "digest": hex0x(attestation_digest(fields, CHAIN_ID, ATTESTATION_CONTRACT)),
            },
            "mldsa": {"scheme": "ML-DSA-44", "backend": mldsa.backend,
                      "publicKey": bundle.b64(mldsa.public_key), "signature": bundle.b64(mldsa_sig)},
            "pqSigHash": hex0x(pq_sig_hash),
            "pqKeyHash": hex0x(keccak256(mldsa.public_key)),
            "aesKeyB64url": bundle.b64url(key),
            "bundleJson": bundle_bytes.decode("utf-8"),
            "cid": bundle.cid_v1_raw(bundle_bytes),
            "cidDigest": hex0x(bundle.sha256(bundle_bytes)),
            "verifyFragment": f"#k={bundle.b64url(key)}",
            "tampered": {
                "description": "productionTonnes 5000 -> 5001 (one digit)",
                "document": tampered,
                "canonical": tampered_canonical.decode("utf-8"),
                "commitment": hex0x(tampered_commitment),
                "recoveredSigner": tampered_signer,
                "expect": {"commitmentMatches": False, "ecdsaValid": False, "mldsaValid": False},
            },
            "expect": {"commitmentMatches": True, "ecdsaValid": True, "mldsaValid": True},
            "seeRounding": {
                "rule": "seeDeclared = round_half_up(Decimal(shortest round-trip decimal string of the number) * 10^4); "
                        "never binary-float Math.round(x * 1e4)",
                "cases": [{"see": s, "seeDeclared": see_to_chain(float(s))} for s in SEE_VECTORS],
            },
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vector, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (cid {vector['cid']}, backend {vector['mldsa']['backend']})")


if __name__ == "__main__":
    main()
