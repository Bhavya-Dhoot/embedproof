import base64
import hashlib
import json

import pytest

from attestor import bundle
from attestor.bundle import BundleError

HELLO_CID = "bafkreibm6jg3ux5qumhcn2b3flc3tyu6dmlb4xa7u5bf44yegnrjhc4yeq"  # CIDv1 raw sha2-256 of b"hello"
EMPTY_CID = "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"  # ... of b""


def test_cid_known_vectors():
    assert bundle.cid_v1_raw(b"hello") == HELLO_CID
    assert bundle.cid_v1_raw(b"") == EMPTY_CID


def test_cid_independent_decode():
    # Rebuild the multihash by hand from hashlib and decode the vector with the stdlib only.
    raw = base64.b32decode(HELLO_CID[1:].upper() + "=" * 6)
    assert raw[:2] == b"\x01\x55"  # CIDv1, raw codec
    assert raw[2:4] == b"\x12\x20"  # sha2-256, 32 bytes
    assert raw[4:] == hashlib.sha256(b"hello").digest()
    assert raw[4:].hex() == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_digest_from_cid_roundtrip_and_strictness():
    assert bundle.digest_from_cid(HELLO_CID) == hashlib.sha256(b"hello").digest()
    for bad in ("", "Qm" + "a" * 44, HELLO_CID.upper(), HELLO_CID[:-1], HELLO_CID + "a", "../" + "a" * 56,
                "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"):  # dag-pb, not raw
        with pytest.raises(BundleError):
            bundle.digest_from_cid(bad)
    # non-canonical trailing bits must not alias to the same digest
    tail = "abcdefghijklmnopqrstuvwxyz234567".index(HELLO_CID[-1])
    alias = HELLO_CID[:-1] + "abcdefghijklmnopqrstuvwxyz234567"[tail ^ 1]
    with pytest.raises(BundleError):
        bundle.digest_from_cid(alias)


def test_aes_round_trip_and_sizes():
    key, aad, pt = bundle.new_key(), b"\x01" * 32, b'{"canonical":true}'
    iv, ct = bundle.encrypt(pt, key, aad)
    assert len(iv) == 12 and len(ct) == len(pt) + 16
    assert bundle.decrypt(ct, key, iv, aad) == pt


def test_aes_fresh_iv_each_time():
    key = bundle.new_key()
    assert bundle.encrypt(b"x", key, b"a")[0] != bundle.encrypt(b"x", key, b"a")[0]


def test_aes_wrong_key_aad_iv_or_ciphertext_fails():
    key, aad = bundle.new_key(), b"\x02" * 32
    iv, ct = bundle.encrypt(b"secret report", key, aad)
    with pytest.raises(BundleError):
        bundle.decrypt(ct, bundle.new_key(), iv, aad)
    with pytest.raises(BundleError):
        bundle.decrypt(ct, key, iv, b"\x03" * 32)
    with pytest.raises(BundleError):
        bundle.decrypt(ct, key, bytes(12), aad)
    flipped = bytearray(ct)
    flipped[0] ^= 1
    with pytest.raises(BundleError):
        bundle.decrypt(bytes(flipped), key, iv, aad)
    with pytest.raises(BundleError):
        bundle.decrypt(ct[:-1], key, iv, aad)
    with pytest.raises(BundleError):
        bundle.decrypt(ct, key[:-1], iv, aad)


def test_aes_nist_style_known_answer():
    # AES-256-GCM known answer (NIST SP 800-38D test case 14: zero key, zero IV, zero plaintext block)
    iv, ct = bundle.encrypt(b"\x00" * 16, bytes(32), b"", iv=bytes(12))
    assert ct.hex() == "cea7403d4d606b6e074ec5d3baf39d18" + "d0d1c8a799996bf0265b98b5d48ab919"


def test_b64url_helpers():
    raw = bytes(range(256))
    assert bundle.b64url_decode(bundle.b64url(raw)) == raw
    assert "=" not in bundle.b64url(b"\xff\xfe")
    with pytest.raises(BundleError):
        bundle.b64_decode("not base64!!", "x")


def _make(**over):
    args = dict(chain_id=31337, installation_id="TR-EAF-0001", period=202609, commitment=b"\x07" * 32,
                ecdsa_signer="0x" + "11" * 20, ecdsa_signature="0x" + "22" * 65, mldsa_backend="pqclean",
                mldsa_public_key=b"\x05" * 1312, mldsa_signature=b"\x06" * 2420, canonical=b'{"a":1}',
                key=bytes(range(32)), iv=bytes(12))
    args.update(over)
    return bundle.build_bundle(**args)


def test_bundle_structure_matches_interfaces():
    raw = _make()
    b = json.loads(raw)
    assert list(b) == ["schema", "algId", "batchHint", "commitment", "ecdsa", "mldsa", "encryption"]
    assert b["schema"] == "embedproof.bundle.v1" and b["algId"] == 1
    assert b["batchHint"] == {"chainId": 31337, "installationId": "TR-EAF-0001", "period": 202609}
    assert b["commitment"] == "0x" + "07" * 32
    assert b["ecdsa"]["scheme"] == "secp256k1-eip191-keccak256"
    assert b["mldsa"]["scheme"] == "ML-DSA-44" and b["mldsa"]["backend"] == "pqclean"
    assert len(base64.b64decode(b["mldsa"]["publicKey"])) == 1312
    assert len(base64.b64decode(b["mldsa"]["signature"])) == 2420
    assert b["encryption"]["alg"] == "AES-256-GCM" and b["encryption"]["aad"] == "commitment"
    assert len(base64.b64decode(b["encryption"]["iv"])) == 12


def test_bundle_open_binds_commitment_as_aad():
    key = bytes(range(32))
    parsed = bundle.parse_bundle(_make())
    assert bundle.open_bundle(parsed, key) == b'{"a":1}'
    # a bundle whose commitment field was swapped must not decrypt (AAD = commitment bytes)
    swapped = json.loads(_make())
    swapped["commitment"] = "0x" + "08" * 32
    with pytest.raises(BundleError):
        bundle.open_bundle(bundle.parse_bundle(json.dumps(swapped).encode()), key)
    with pytest.raises(BundleError):
        bundle.open_bundle(parsed, bytes(32))


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(schema="other"), lambda b: b.update(algId=2), lambda b: b["encryption"].update(alg="AES-128-CBC"),
    lambda b: b["mldsa"].update(scheme="Dilithium2"), lambda b: b.update(commitment="0x1234"),
    lambda b: b["mldsa"].update(signature="***"), lambda b: b.pop("ecdsa"),
])
def test_parse_bundle_rejects_malformed(mutate):
    b = json.loads(_make())
    mutate(b)
    with pytest.raises(BundleError):
        bundle.parse_bundle(json.dumps(b).encode())
    with pytest.raises(BundleError):
        bundle.parse_bundle(b"not json")
