"""Generate a cross-language test vector for web/src/lib/verify.ts (docs/INTERFACES.md sections 5 and 9).

Run from the repo root:
    PYTHONPATH=. .venv/Scripts/python.exe web/tests/gen_vector.py

JCS (rfc8785) -> keccak256 commitment -> pure ML-DSA-44 (empty context) over the canonical bytes ->
EIP-712 attestor signature (domain EmbedProof/1/chainId/attestation proxy; Attestation struct, no
cidDigest) over the on-chain-style fields -> AES-256-GCM (AAD = commitment bytes) -> bundle ->
CIDv1(raw, sha2-256). Also writes round-half-up SEE cases computed with Python's Decimal(repr(x)).
"""
import base64, hashlib, json, os, pathlib
from decimal import ROUND_HALF_UP, Decimal

import rfc8785
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eth_account import Account
from eth_account.messages import _hash_eip191_message, encode_defunct, encode_typed_data
from eth_utils import keccak
from pqcrypto.sign import ml_dsa_44

OUT = pathlib.Path(__file__).parent / "vectors" / "bundle_vector.json"
# public Anvil test key #1 (attestor service key) -- never use on a real network
ATTESTOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
EXPORTER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # Anvil #2
CHAIN_ID = 31337
ATTESTATION_PROXY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

b64 = lambda b: base64.b64encode(b).decode()
b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
hx = lambda b: "0x" + b.hex()

report = {
    "schema": "embedproof.report.v1",
    "installationId": "TR-EAF-0001",
    "period": "2026-09",
    "route": "EAF",
    "cnCode": "72083900",
    "productionTonnes": 5000,
    "directEmissionsTco2": 1150.0,
    "electricityMwh": 2250.0,
    "gridFactorTco2PerMwh": 0.42,
    "indirectEmissionsTco2": 945.0,
    "seeTco2PerT": 0.419,
}
MODEL_HASH = "0x" + "ab" * 32
RISK = 12
assessment = {
    "riskScore": RISK, "band": "green",
    "q05": 0.3712, "q50": 0.4466, "q95": 0.5821,
    "expectedLow": 0.3712, "expectedHigh": 0.5821, "seeDeclared": 0.419,
    "cbamDefault": 1.9, "belowHalfDefault": False,
    "modelVersionHash": MODEL_HASH,
    "shap": [
        {"feature": "route", "label": "Production route", "value": "EAF", "contribution": -0.41},
        {"feature": "grid_factor", "label": "Grid factor", "value": 0.42, "contribution": 0.30000000000000004},
        {"feature": "capacity_tpy", "label": "Capacity (t/yr)", "value": 100000, "contribution": 1.5e-7},
    ],
    "explanation": "Declared 0.419 tCO2/t sits inside the expected EAF range 0.371 to 0.582 (çelik).",
}
doc = {
    "schema": "embedproof.attestation.v1",
    "report": report,
    "assessment": assessment,
    "chainId": CHAIN_ID,
    "attestationContract": ATTESTATION_PROXY,
    "salt": "0x" + "5a" * 32,
    "issuedAt": "2026-10-18T12:00:00Z",
}

# extra JCS edge cases (numbers, escapes, key order by UTF-16 code unit)
jcs_cases_in = [
    {"name": "numbers", "input": {"a": 0.42, "b": 1150.0, "c": 1e21, "d": 1e-7, "e": -0.0, "f": 123456789012345680000.0,
                                  "g": 0.30000000000000004, "h": 5e-7, "i": 4.35, "j": 100, "k": 1.0e-6, "l": 0.000001234}},
    {"name": "strings", "input": {"s": "çelik \"q\" \\ / \u0001 \u001f \n \t \u007f  ", "emoji": "\U0001F600"}},
    {"name": "key-order", "input": {"￿": 1, "\U0001F600": 2, "b": 3, "a": 4, "B": 5, "ç": 6, "10": 7, "9": 8}},
    {"name": "nested", "input": {"z": [3, 2.50, {"y": None, "x": True, "w": False}], "a": {"b": {"c": []}}}},
]
jcs_cases = [{"name": c["name"], "input": c["input"], "canonical": rfc8785.dumps(c["input"]).decode("utf-8")} for c in jcs_cases_in]

# INTERFACES 9G: round_half_up(Decimal(repr(x)) * 10^4); the float product is wrong for 0.40045 (4004.4999...)
see_cases = []
for x in [0.419, 0.40045, 0.55, 1.00005, 4.35, 0.00005, 0.000049999, 1e-7, 1.5e-7, 2.17495, 0.30000000000000004, 1.9, 123.45675, 0.0, 5e-5, 1e21]:
    d = (Decimal(repr(x)) * 10**4).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    see_cases.append({"x": x, "repr": repr(x), "seeDeclared": int(d)})

canonical = rfc8785.dumps(doc)
commitment = keccak(canonical)

pk, sk = ml_dsa_44.keygen()
mldsa_sig = ml_dsa_44.sign(sk, canonical)
assert ml_dsa_44.verify(pk, canonical, mldsa_sig) is None  # None == success
pq_sig_hash = keccak(mldsa_sig)

# EIP-712 Attestation signed by the attestor over fields the chain stores
installation_id = b"TR-EAF-0001".ljust(32, b"\0")
onchain = {
    "installationId": hx(installation_id), "period": 202609, "commitment": hx(commitment), "seeDeclared": 4190,
    "verifiedTonnes": 5000, "riskScore": RISK, "modelVersionHash": MODEL_HASH, "algId": 1, "pqSigHash": hx(pq_sig_hash),
}
typed = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
        ],
        "Attestation": [
            {"name": "installationId", "type": "bytes32"}, {"name": "period", "type": "uint32"},
            {"name": "commitment", "type": "bytes32"}, {"name": "seeDeclared", "type": "uint32"},
            {"name": "verifiedTonnes", "type": "uint64"}, {"name": "riskScore", "type": "uint8"},
            {"name": "modelVersionHash", "type": "bytes32"}, {"name": "algId", "type": "uint8"},
            {"name": "pqSigHash", "type": "bytes32"},
        ],
    },
    "primaryType": "Attestation",
    "domain": {"name": "EmbedProof", "version": "1", "chainId": CHAIN_ID, "verifyingContract": ATTESTATION_PROXY},
    "message": {
        "installationId": installation_id, "period": 202609, "commitment": commitment, "seeDeclared": 4190,
        "verifiedTonnes": 5000, "riskScore": RISK, "modelVersionHash": bytes.fromhex(MODEL_HASH[2:]), "algId": 1, "pqSigHash": pq_sig_hash,
    },
}
signable = encode_typed_data(full_message=typed)
sig = Account.sign_message(signable, ATTESTOR_KEY)
ecdsa_sig = sig.signature.hex()
ecdsa_sig = ecdsa_sig if ecdsa_sig.startswith("0x") else "0x" + ecdsa_sig
eip712_digest = _hash_eip191_message(signable)
attestor = Account.from_key(ATTESTOR_KEY).address

key = os.urandom(32)
iv = os.urandom(12)
ct = AESGCM(key).encrypt(iv, canonical, commitment)  # ciphertext || 16-byte tag

bundle = {
    "schema": "embedproof.bundle.v1",
    "algId": 1,
    "batchHint": {"chainId": CHAIN_ID, "installationId": "TR-EAF-0001", "period": 202609},
    "commitment": hx(commitment),
    "ecdsa": {"scheme": "eip712-embedproof-attestation-v1", "signer": attestor, "signature": ecdsa_sig},
    "mldsa": {"scheme": "ML-DSA-44", "backend": "pqclean", "publicKey": b64(pk), "signature": b64(mldsa_sig)},
    "encryption": {"alg": "AES-256-GCM", "iv": b64(iv), "aad": "commitment", "ciphertext": b64(ct)},
}
bundle_bytes = json.dumps(bundle, separators=(",", ":")).encode("utf-8")
digest = hashlib.sha256(bundle_bytes).digest()
cid = "b" + base64.b32encode(bytes([0x01, 0x55, 0x12, 0x20]) + digest).decode().lower().rstrip("=")

# the exporter's EIP-191 signature over keccak256(JCS(report)) (unchanged by v1.1)
report_digest = keccak(rfc8785.dumps(report))
exporter_sig = Account.sign_message(encode_defunct(primitive=report_digest), EXPORTER_KEY).signature.hex()

typed_json = {
    "domain": typed["domain"],
    "types": {"Attestation": typed["types"]["Attestation"]},
    "primaryType": "Attestation",
    "message": {**onchain, "verifiedTonnes": str(onchain["verifiedTonnes"])},
}
vector = {
    "meta": {
        "generator": "web/tests/gen_vector.py",
        "libs": "rfc8785 + eth_account (EIP-712) + cryptography AESGCM + pqcrypto ml_dsa_44 (PQClean)",
        "note": "ATTESTOR_KEY / EXPORTER_KEY are public Anvil test keys. ML-DSA keys are throwaway.",
    },
    "chainId": CHAIN_ID,
    "attestationContract": ATTESTATION_PROXY,
    "jcsCases": jcs_cases,
    "seeCases": see_cases,
    "document": doc,
    "canonical": canonical.decode("utf-8"),
    "commitment": hx(commitment),
    "attestor": attestor,
    "eip712": {**typed_json, "digest": hx(eip712_digest)},
    "onchain": onchain,
    "ecdsaSignature": ecdsa_sig,
    "exporter": {"address": Account.from_key(EXPORTER_KEY).address, "reportDigest": hx(report_digest), "signature": exporter_sig if exporter_sig.startswith("0x") else "0x" + exporter_sig},
    "mldsaPublicKeyB64": b64(pk),
    "mldsaSignatureB64": b64(mldsa_sig),
    "pqSigHash": hx(pq_sig_hash),
    "keyB64url": b64u(key),
    "bundle": bundle,
    "bundleBytesB64": b64(bundle_bytes),
    "cidDigest": hx(digest),
    "cid": cid,
}
OUT.write_text(json.dumps(vector, indent=2), encoding="utf-8")
print("wrote", OUT, "| cid", cid, "| commitment", hx(commitment), "| eip712 digest", hx(eip712_digest))
