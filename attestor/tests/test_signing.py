import pytest
from eth_account import Account

from attestor import signing
from attestor.canonical import commitment_of, jcs
from attestor.signing import (ML_DSA_44_PK_LEN, ML_DSA_44_SIG_LEN, MLDSASigner, SignatureError,
                              recover_commitment_signer, recover_text_signer, sign_commitment)

from .conftest import ADDR, KEYS

COMMITMENT = commitment_of(jcs({"a": 1}))


def test_anvil_keys_match_interfaces_table():
    # docs/INTERFACES.md section 1
    assert ADDR["attestor"] == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    assert ADDR["exporterA"] == "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    assert ADDR["exporterB"] == "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    assert ADDR["verifier"] == "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
    assert ADDR["importerA"] == "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc"
    assert ADDR["importerB"] == "0x976EA74026E726554dB657fA54763abd0C3a0aa9"
    assert ADDR["regulator"] == "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955"


def test_ecdsa_sign_and_recover():
    sig, signer = sign_commitment(COMMITMENT, KEYS["attestor"])
    assert signer == ADDR["attestor"]
    assert len(bytes.fromhex(sig[2:])) == 65 and sig[-2:] in ("1b", "1c")  # v = 27/28 as OZ ECDSA.recover expects
    assert recover_commitment_signer(COMMITMENT, sig) == signer


def test_ecdsa_other_message_recovers_other_address():
    sig, signer = sign_commitment(COMMITMENT, KEYS["attestor"])
    other = commitment_of(jcs({"a": 2}))
    assert recover_commitment_signer(other, sig) != signer


def test_ecdsa_is_eip191_over_the_raw_32_bytes():
    from eth_account.messages import encode_defunct

    sig, signer = sign_commitment(COMMITMENT, KEYS["attestor"])
    assert Account.recover_message(encode_defunct(primitive=COMMITMENT), signature=sig) == signer
    # a signature over the hex *text* of the commitment must not verify as the raw-bytes one
    assert recover_text_signer("0x" + COMMITMENT.hex(), sig) != signer


@pytest.mark.parametrize("bad", ["", "0x", "0x" + "00" * 65, "zz", "0x" + "11" * 64, None, 5])
def test_ecdsa_rejects_garbage_signatures(bad):
    with pytest.raises(SignatureError):
        recover_commitment_signer(COMMITMENT, bad)


def test_commitment_must_be_32_bytes():
    with pytest.raises(SignatureError):
        sign_commitment(b"short", KEYS["attestor"])


# ------------------------------------------------------------------ ML-DSA-44


@pytest.fixture(scope="module")
def signer(tmp_path_factory):
    return MLDSASigner.load_or_create(tmp_path_factory.mktemp("keys"))


def test_mldsa_sizes_and_roundtrip(signer):
    msg = jcs({"hello": "world"})
    sig = signer.sign(msg)
    assert len(signer.public_key) == ML_DSA_44_PK_LEN == 1312
    assert len(sig) == ML_DSA_44_SIG_LEN == 2420
    assert signer.verify(signer.public_key, msg, sig)


def test_mldsa_tamper_fails(signer):
    msg = jcs({"hello": "world"})
    sig = signer.sign(msg)
    assert not signer.verify(signer.public_key, jcs({"hello": "worle"}), sig)  # message edited
    flipped = bytearray(sig)
    flipped[100] ^= 1
    assert not signer.verify(signer.public_key, msg, bytes(flipped))  # signature edited
    assert not signer.verify(signer.public_key, msg, sig[:-1])  # truncated
    assert not signer.verify(signer.public_key[:-1], msg, sig)  # malformed key


def test_mldsa_wrong_key_fails(signer, tmp_path):
    other = MLDSASigner.load_or_create(tmp_path)
    msg = b"m"
    assert not signer.verify(other.public_key, msg, signer.sign(msg))


def test_mldsa_key_is_persistent(tmp_path):
    a = MLDSASigner.load_or_create(tmp_path)
    b = MLDSASigner.load_or_create(tmp_path)
    assert a.public_key == b.public_key
    assert b.verify(a.public_key, b"x", a.sign(b"x"))


def test_mldsa_corrupt_key_files_fail_loudly(tmp_path):
    MLDSASigner.load_or_create(tmp_path)
    (tmp_path / "mldsa44.sk").write_bytes(b"short")
    with pytest.raises(RuntimeError, match="corrupt"):
        MLDSASigner.load_or_create(tmp_path)


def test_mismatched_key_pair_fails_loudly(tmp_path):
    a = MLDSASigner.load_or_create(tmp_path / "a")
    MLDSASigner.load_or_create(tmp_path / "b")
    (tmp_path / "b" / "mldsa44.pk").write_bytes(a.public_key)
    with pytest.raises(RuntimeError, match="self-test"):
        MLDSASigner.load_or_create(tmp_path / "b")


def test_backend_selection_order(monkeypatch):
    monkeypatch.delenv("PQ_BACKEND", raising=False)
    backend = signing.select_backend()
    assert backend.name in ("oqs", "pqcrypto")  # oqs absent here -> PQClean; never the reference impl by default
    assert signing.select_backend(("dilithium_py",)).label == "dilithium-py-reference"
    assert signing.select_backend(("nope", "pqcrypto")).label == "pqclean"  # unknown/unavailable names are skipped
    with pytest.raises(RuntimeError, match="no ML-DSA-44 backend"):
        signing.select_backend(("nope",))
    monkeypatch.setenv("PQ_BACKEND", "dilithium_py")
    assert signing.select_backend().name == "dilithium_py"


def test_backends_interoperate():
    pqcrypto, dilithium = signing._load_pqcrypto(), signing._load_dilithium_py()
    pk, sk = pqcrypto.keygen()
    msg = b"interop"
    assert dilithium.verify(pk, msg, pqcrypto.sign(sk, msg))  # PQClean signature, reference verifier
    assert pqcrypto.verify(pk, msg, dilithium.sign(sk, msg))  # reference signature, PQClean verifier (same sk)
