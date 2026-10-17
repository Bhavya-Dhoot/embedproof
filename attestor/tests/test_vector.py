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


def test_ecdsa_over_commitment():
    commitment = bytes.fromhex(VECTOR["commitment"][2:])
    signer = Account.recover_message(encode_defunct(primitive=commitment), signature=VECTOR["ecdsa"]["signature"])
    assert signer == VECTOR["ecdsa"]["signer"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


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
    signer = Account.recover_message(encode_defunct(primitive=bytes.fromhex(t["commitment"][2:])),
                                     signature=VECTOR["ecdsa"]["signature"])
    assert signer != VECTOR["ecdsa"]["signer"]
    diff = [k for k in VECTOR["document"]["report"] if VECTOR["document"]["report"][k] != t["document"]["report"][k]]
    assert diff == ["productionTonnes"]
