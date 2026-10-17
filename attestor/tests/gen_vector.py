"""Generate attestor/tests/vectors/bundle_vector.json (a full worked example for other implementations).

    PYTHONPATH=. python attestor/tests/gen_vector.py

Everything is produced by the production code paths (attestor.canonical/signing/bundle). Randomness is
pinned where the format allows it (salt, IV, AES key, issuedAt); the ML-DSA-44 key pair and signature are
fresh per run, which is fine because the file carries both. The `assessment` block is a fixed example,
NOT output of the trained model.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from attestor import bundle
from attestor.canonical import commitment_of, hex0x, jcs, keccak256
from attestor.signing import MLDSASigner, recover_commitment_signer, sign_commitment

ATTESTOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # Anvil #1 (public test key)
EXPORTER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # Anvil #2 (public test key)
ATTESTATION_CONTRACT = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
OUT = Path(__file__).resolve().parent / "vectors" / "bundle_vector.json"

REPORT = {
    "schema": "embedproof.report.v1", "installationId": "TR-EAF-0001", "period": "2026-09", "route": "EAF",
    "cnCode": "72083900", "productionTonnes": 5000, "directEmissionsTco2": 1150.0, "electricityMwh": 2250.0,
    "gridFactorTco2PerMwh": 0.42, "indirectEmissionsTco2": 945.0, "seeTco2PerT": 0.419,
}
ASSESSMENT = {
    "riskScore": 4, "band": "green", "q05": 0.318, "q50": 0.452, "q95": 0.67, "expectedLow": 0.318,
    "expectedHigh": 0.67, "seeDeclared": 0.419, "cbamDefault": 1.3, "belowHalfDefault": True,
    "modelVersionHash": "0x" + "ab" * 32,
    "shap": [{"feature": "route", "label": "Production route", "value": "EAF", "contribution": -0.41}],
    "explanation": "Fixed example assessment for the test vector; not produced by the trained model.",
}


def main() -> None:
    document = {
        "schema": "embedproof.attestation.v1", "report": REPORT, "assessment": ASSESSMENT, "chainId": 31337,
        "attestationContract": ATTESTATION_CONTRACT, "salt": "0x" + bytes(range(32)).hex(),
        "issuedAt": "2026-10-18T12:00:00Z",
    }
    canonical = jcs(document)
    commitment = commitment_of(canonical)
    ecdsa_sig, ecdsa_signer = sign_commitment(commitment, ATTESTOR_KEY)
    assert recover_commitment_signer(commitment, ecdsa_sig) == ecdsa_signer

    with tempfile.TemporaryDirectory() as tmp:
        mldsa = MLDSASigner.load_or_create(Path(tmp))
        mldsa_sig = mldsa.sign(canonical)
        assert mldsa.verify(mldsa.public_key, canonical, mldsa_sig)

        key = bytes(range(100, 132))
        bundle_bytes = bundle.build_bundle(
            chain_id=31337, installation_id=REPORT["installationId"], period=202609, commitment=commitment,
            ecdsa_signer=ecdsa_signer, ecdsa_signature=ecdsa_sig, mldsa_backend=mldsa.backend,
            mldsa_public_key=mldsa.public_key, mldsa_signature=mldsa_sig, canonical=canonical, key=key,
            iv=bytes(range(12)))

        # A one-digit edit of the document: productionTonnes 5000 -> 5001.
        tampered = json.loads(canonical)
        tampered["report"]["productionTonnes"] = 5001
        tampered_canonical = jcs(tampered)

        report_digest = keccak256(jcs(REPORT))
        exporter_sig, exporter = sign_commitment(report_digest, EXPORTER_KEY)
        vector = {
            "_comment": [
                "EmbedProof bundle test vector (docs/INTERFACES.md section 5).",
                "Keys are public Anvil test keys. The assessment block is a fixed example, not model output.",
                "To check in a browser: canonicalize(document) == canonical; keccak256(utf8(canonical)) == commitment;",
                "viem recoverMessageAddress({message:{raw:commitment}, signature}) == ecdsa.signer;",
                "@noble/post-quantum ml_dsa44.verify over utf8(canonical) with mldsa.publicKey/signature;",
                "AES-256-GCM decrypt bundle.encryption.ciphertext (ciphertext||tag) with key=base64url(aesKeyB64url),",
                "iv=base64(encryption.iv), AAD=the 32 commitment bytes -> utf8(canonical).",
            ],
            "chainId": 31337,
            "attestationContract": ATTESTATION_CONTRACT,
            "report": REPORT,
            "exporter": {"address": exporter, "reportDigest": hex0x(report_digest), "signature": exporter_sig,
                         "note": "EIP-191 personal_sign over the 32 raw bytes keccak256(JCS(report))"},
            "document": document,
            "canonical": canonical.decode("utf-8"),
            "commitment": hex0x(commitment),
            "ecdsa": {"scheme": "secp256k1-eip191-keccak256", "signer": ecdsa_signer, "signature": ecdsa_sig},
            "mldsa": {"scheme": "ML-DSA-44", "backend": mldsa.backend,
                      "publicKey": bundle.b64(mldsa.public_key), "signature": bundle.b64(mldsa_sig)},
            "pqSigHash": hex0x(keccak256(mldsa_sig)),
            "pqKeyHash": hex0x(keccak256(mldsa.public_key)),  # what registry.attestorPqKey(signer) must hold
            "aesKeyB64url": bundle.b64url(key),
            "bundleJson": bundle_bytes.decode("utf-8"),
            "cid": bundle.cid_v1_raw(bundle_bytes),
            "cidDigest": hex0x(bundle.sha256(bundle_bytes)),
            "verifyFragment": f"#k={bundle.b64url(key)}",
            "tampered": {
                "description": "productionTonnes 5000 -> 5001 (one digit)",
                "document": tampered,
                "canonical": tampered_canonical.decode("utf-8"),
                "commitment": hex0x(commitment_of(tampered_canonical)),
                "expect": {"commitmentMatches": False, "ecdsaValid": False, "mldsaValid": False},
            },
            "expect": {"commitmentMatches": True, "ecdsaValid": True, "mldsaValid": True},
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vector, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (cid {vector['cid']}, backend {vector['mldsa']['backend']})")


if __name__ == "__main__":
    main()
