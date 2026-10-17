import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from attestor.disclosure import WINDOW_SECONDS, DisclosureAuthError, authenticate, build_message

from .conftest import ADDR, KEYS

NOW = 1_790_000_000


def sign(msg, key="exporterA"):
    return "0x" + Account.sign_message(encode_defunct(text=msg), KEYS[key]).signature.hex()


def auth(batch=7, msg=None, key="exporterA", address=None, now=NOW, path_batch=None):
    msg = msg if msg is not None else build_message(batch, NOW)
    return authenticate(path_batch if path_batch is not None else batch, address or ADDR[key], msg, sign(msg, key), now)


def fails(status, **kw):
    with pytest.raises(DisclosureAuthError) as e:
        auth(**kw)
    assert e.value.status == status, e.value.detail
    return e.value.detail


def test_message_format_is_exact():
    assert build_message(12, 1790000000) == "EmbedProof disclosure\nbatch:12\nts:1790000000"


def test_valid_signature_returns_checksum_signer():
    assert auth() == ADDR["exporterA"]
    assert auth(address=ADDR["exporterA"].lower()) == ADDR["exporterA"]  # lower-case address accepted


@pytest.mark.parametrize("delta,ok", [(0, True), (299, True), (300, True), (301, False), (-300, True), (-301, False),
                                      (3600, False), (-3600, False)])
def test_timestamp_window(delta, ok):
    assert WINDOW_SECONDS == 300
    if ok:
        auth(now=NOW + delta)
    else:
        assert "window" in fails(403, now=NOW + delta)


def test_wrong_signer_for_stated_address():
    fails(403, key="importerA", address=ADDR["exporterA"])


def test_batch_id_in_message_must_match_path():
    assert "batch id" in fails(403, batch=7, path_batch=8)


@pytest.mark.parametrize("msg", [
    "EmbedProof disclosure\nbatch:7\nts:1790000000\n",  # trailing newline (Python '$' would accept this)
    " EmbedProof disclosure\nbatch:7\nts:1790000000",
    "EmbedProof disclosure\r\nbatch:7\r\nts:1790000000",
    "EmbedProof disclosure\nbatch:07\nts:1790000000",
    "EmbedProof disclosure\nbatch:7\nts:1790000000.5",
    "embedproof disclosure\nbatch:7\nts:1790000000",
    "EmbedProof disclosure\nbatch:-7\nts:1790000000",
    "EmbedProof disclosure\nbatch:7\nts:1790000000\nextra:1",
    "",
])
def test_message_must_match_template_exactly(msg):
    fails(422, msg=msg)


def test_signature_over_different_text_rejected():
    with pytest.raises(DisclosureAuthError) as e:
        authenticate(7, ADDR["exporterA"], build_message(7, NOW), sign(build_message(7, NOW + 1)), NOW)
    assert e.value.status == 403


@pytest.mark.parametrize("sig", ["", "0x12", "0x" + "00" * 65, None])
def test_garbage_signature_rejected(sig):
    with pytest.raises(DisclosureAuthError) as e:
        authenticate(7, ADDR["exporterA"], build_message(7, NOW), sig, NOW)
    assert e.value.status in (403, 422)


@pytest.mark.parametrize("addr", ["", "0x123", "not an address", None, "0x" + "zz" * 20])
def test_invalid_address_rejected(addr):
    with pytest.raises(DisclosureAuthError) as e:
        authenticate(7, addr, build_message(7, NOW), sign(build_message(7, NOW)), NOW)
    assert e.value.status == 422
