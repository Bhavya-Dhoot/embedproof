"""Checks vectors/bundle_vector.json with primitives independent of the attestor package where possible."""
import base64
import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

VECTOR = json.loads((Path(__file__).parent / "vectors" / "bundle_vector.json").read_text(encoding="utf-8"))


def b64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_canonical_and_commitment():
    canonical = rfc8785.dumps(VECTOR["document"])
    assert canonical.decode() == VECTOR["canonical"]
    assert "0x" + keccak(canonical).hex() == VECTOR["commitment"]
    # re-serialising the stored document through plain json (different key order, spacing) changes nothing
    assert rfc8785.dumps(json.loads(json.dumps(VECTOR["document"], indent=7, sort_keys=True))) == canonical


def test_exporter_signature_over_report_digest():
    digest = keccak(rfc8785.dumps(VECTOR["report"]))
    assert "0x" + digest.hex() == VECTOR["exporter"]["reportDigest"]
    assert Account.recover_message(encode_defunct(primitive=digest), signature=VECTOR["exporter"]["signature"]) \
        == VECTOR["exporter"]["address"] == "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def test_eip712_attestation_signature():
    from eth_account.messages import encode_typed_data

    from .test_signing import manual_digest

    e = VECTOR["eip712"]
    assert VECTOR["ecdsa"]["scheme"] == "eip712-embedproof-attestation-v1"
    assert e["domain"] == {"name": "EmbedProof", "version": "1", "chainId": 31337,
                           "verifyingContract": VECTOR["attestationContract"]}
    assert [f["name"] for f in e["types"]["Attestation"]] == [
        "installationId", "period", "commitment", "seeDeclared", "verifiedTonnes", "riskScore", "modelVersionHash",
        "algId", "pqSigHash"]  # no cidDigest
    m = e["message"]
    assert m["commitment"] == VECTOR["commitment"] and m["pqSigHash"] == VECTOR["pqSigHash"]
    assert m["installationId"] == "0x" + b"TR-EAF-0001".ljust(32, b"\x00").hex() and m["period"] == 202609
    assert (m["seeDeclared"], m["verifiedTonnes"], m["riskScore"], m["algId"]) == (
        4190, 5000, VECTOR["document"]["assessment"]["riskScore"], 1)
    assert m["modelVersionHash"] == VECTOR["document"]["assessment"]["modelVersionHash"]
    # the digest, recomputed by hand (eth_abi + keccak) and via eth_account, and the signature over it
    by_hand = manual_digest({k: (bytes.fromhex(v[2:]) if isinstance(v, str) else v) for k, v in m.items()},
                            31337, e["domain"]["verifyingContract"])
    assert "0x" + by_hand.hex() == e["digest"]
    full = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                       {"name": "chainId", "type": "uint256"},
                                       {"name": "verifyingContract", "type": "address"}], **e["types"]},
            "primaryType": "Attestation", "domain": e["domain"], "message": m}
    assert Account.recover_message(encode_typed_data(full_message=full), signature=VECTOR["ecdsa"]["signature"])         == VECTOR["ecdsa"]["signer"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    assert Account._recover_hash(by_hand, signature=VECTOR["ecdsa"]["signature"]) == VECTOR["ecdsa"]["signer"]


def test_see_rounding_cases_follow_the_decimal_string_rule():
    from decimal import ROUND_HALF_UP, Decimal

    cases = VECTOR["seeRounding"]["cases"]
    assert len(cases) >= 10
    for c in cases:
        assert int((Decimal(c["see"]) * 10000).to_integral_value(rounding=ROUND_HALF_UP)) == c["seeDeclared"], c


def test_mldsa_with_independent_reference_implementation():
    from dilithium_py.ml_dsa import ML_DSA_44

    pk, sig = base64.b64decode(VECTOR["mldsa"]["publicKey"]), base64.b64decode(VECTOR["mldsa"]["signature"])
    assert (len(pk), len(sig)) == (1312, 2420)
    canonical = VECTOR["canonical"].encode()
    assert ML_DSA_44.verify(pk, canonical, sig)
    assert not ML_DSA_44.verify(pk, VECTOR["tampered"]["canonical"].encode(), sig)
    assert "0x" + keccak(sig).hex() == VECTOR["pqSigHash"]
    assert "0x" + keccak(pk).hex() == VECTOR["pqKeyHash"]


def test_bundle_decrypts_to_canonical_and_binds_commitment():
    bundle = json.loads(VECTOR["bundleJson"])
    assert bundle["commitment"] == VECTOR["commitment"] and bundle["mldsa"] == VECTOR["mldsa"]
    assert bundle["ecdsa"] == VECTOR["ecdsa"]
    enc = bundle["encryption"]
    key, iv, ct = b64u(VECTOR["aesKeyB64url"]), base64.b64decode(enc["iv"]), base64.b64decode(enc["ciphertext"])
    commitment = bytes.fromhex(VECTOR["commitment"][2:])
    assert len(key) == 32 and len(iv) == 12
    assert AESGCM(key).decrypt(iv, ct, commitment) == VECTOR["canonical"].encode()
    with pytest.raises(Exception):
        AESGCM(key).decrypt(iv, ct, b"\x00" * 32)


def test_cid_and_digest():
    raw = VECTOR["bundleJson"].encode()
    digest = hashlib.sha256(raw).digest()
    assert "0x" + digest.hex() == VECTOR["cidDigest"]
    cid = "b" + base64.b32encode(bytes([1, 0x55, 0x12, 0x20]) + digest).decode().lower().rstrip("=")
    assert cid == VECTOR["cid"] and cid.startswith("bafkrei")


def test_tampered_case():
    t = VECTOR["tampered"]
    assert rfc8785.dumps(t["document"]).decode() == t["canonical"] != VECTOR["canonical"]
    assert "0x" + keccak(t["canonical"].encode()).hex() == t["commitment"] != VECTOR["commitment"]
    # a verifier re-builds the typed data with the RECOMPUTED commitment; the old signature then recovers elsewhere
    from eth_account.messages import encode_typed_data

    full = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                       {"name": "chainId", "type": "uint256"},
                                       {"name": "verifyingContract", "type": "address"}], **VECTOR["eip712"]["types"]},
            "primaryType": "Attestation", "domain": VECTOR["eip712"]["domain"],
            "message": {**VECTOR["eip712"]["message"], "commitment": t["commitment"]}}
    signer = Account.recover_message(encode_typed_data(full_message=full), signature=VECTOR["ecdsa"]["signature"])
    assert signer != VECTOR["ecdsa"]["signer"] and signer == t["recoveredSigner"]
    diff = [k for k in VECTOR["document"]["report"] if VECTOR["document"]["report"][k] != t["document"]["report"][k]]
    assert diff == ["productionTonnes"]


def test_assessment_carries_history_flags_and_the_recorded_inputs():
    a = VECTOR["document"]["assessment"]
    assert a["hasHistory"] is True and a["flags"] == [] and "latencyMs" not in a
    i = a["inputs"]
    assert i["artefactSha256"] == a["modelVersionHash"] and len(i["artefactSha256"]) == 66
    assert i["features"] == {"route": "EAF", "capacityTpy": 300000, "gridGco2PerKwh": 475.73, "ctLaggedPriorEf": 0.08}
    assert i["prior"]["sourceAssetId"] == 1567079 and i["prior"]["value"] == 0.08
    assert i["grid"]["observationYear"] == 2025 and i["installation"]["id"] == "TR-EAF-0001"
    assert i["dataVersion"]["source"].startswith("Climate TRACE v5.10.0")


def test_the_recorded_inputs_reproduce_the_signed_score_with_the_frozen_model():
    """What `python -m attestor.rescore` does, on the vector document."""
    from attestor.rescore import rescore_document

    from .conftest import ARTEFACT

    a = VECTOR["document"]["assessment"]
    out = rescore_document(VECTOR["document"], ARTEFACT)
    assert (out["riskScore"], out["band"], out["q05"], out["q50"], out["q95"]) == (
        a["riskScore"], a["band"], a["q05"], a["q50"], a["q95"])
    assert out["modelVersionHash"] == a["modelVersionHash"]
