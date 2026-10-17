import json
import math
import struct

import pytest

from attestor.canonical import CanonicalizationError, commitment_of, jcs, keccak256, to_jsonable

from .conftest import HONEST_REPORT

DOC = {
    "schema": "embedproof.attestation.v1",
    "report": HONEST_REPORT,
    "assessment": {"riskScore": 4, "band": "green", "q05": 0.35, "shap": [{"feature": "route", "contribution": -0.41}]},
    "chainId": 31337,
    "attestationContract": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
    "salt": "0x" + "11" * 32,
    "issuedAt": "2026-10-18T12:00:00Z",
}


def test_keccak_known_vector():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_key_order_does_not_change_commitment():
    shuffled = json.loads(json.dumps(DOC, sort_keys=True))
    reversed_doc = dict(reversed(list(DOC.items())))
    reversed_doc["report"] = dict(reversed(list(DOC["report"].items())))
    assert jcs(shuffled) == jcs(DOC) == jcs(reversed_doc)
    assert commitment_of(jcs(shuffled)) == commitment_of(jcs(DOC))


def test_whitespace_and_reparse_do_not_change_commitment():
    base = commitment_of(jcs(DOC))
    for text in (json.dumps(DOC, indent=4), json.dumps(DOC, separators=(",", ":")), json.dumps(DOC, indent="\t")):
        assert commitment_of(jcs(json.loads(text))) == base
    # JSON text with unusual-but-legal whitespace and escapes
    messy = json.dumps(DOC, indent=3, separators=(" ,", " : ")).replace("\n", "\r\n")
    assert commitment_of(jcs(json.loads(messy))) == base


def test_float_formatting_is_irrelevant_to_the_commitment():
    a = json.loads('{"x": 1150.0, "y": 0.42, "z": 5000}')
    b = json.loads('{"x": 1.15e3, "y": 4.2e-1, "z": 5000.0}')
    c = json.loads('{"z": 5e3, "y": 0.420, "x": 1150}')
    assert jcs(a) == jcs(b) == jcs(c) == b'{"x":1150,"y":0.42,"z":5000}'


@pytest.mark.parametrize("bits,expected", [
    # RFC 8785 Appendix B (ES6 number serialisation)
    (0x0000000000000000, "0"), (0x8000000000000000, "0"), (0x0000000000000001, "5e-324"),
    (0x7FEFFFFFFFFFFFFF, "1.7976931348623157e+308"), (0x4340000000000000, "9007199254740992"),
    (0x4430000000000000, "295147905179352830000"), (0x44B52D02C7E14AF5, "9.999999999999997e+22"),
    (0x44B52D02C7E14AF6, "1e+23"), (0x44B52D02C7E14AF7, "1.0000000000000001e+23"),
    (0x444B1AE4D6E2EF4E, "999999999999999700000"), (0x444B1AE4D6E2EF4F, "999999999999999900000"),
    (0x444B1AE4D6E2EF50, "1e+21"), (0x3EB0C6F7A0B5ED8C, "9.999999999999997e-7"),
    (0x3EB0C6F7A0B5ED8D, "0.000001"),
])
def test_rfc8785_number_vectors(bits, expected):
    value = struct.unpack(">d", struct.pack(">Q", bits))[0]
    assert jcs(value).decode() == expected


def test_utf16_code_unit_key_ordering():
    # RFC 8785 sorts keys by UTF-16 code units: U+1F600 (D83D DE00) sorts before U+FB01.
    assert jcs({"ﬁ": 1, "\U0001f600": 2}).decode() == '{"\U0001f600":2,"ﬁ":1}'


def test_string_escaping_and_utf8():
    assert jcs({"k": "a \n\"\\é"}) == '{"k":"a \\n\\"\\\\é"}'.encode("utf-8")


def test_scorer_numpy_values_are_normalised():
    np = pytest.importorskip("numpy")
    assert jcs({"a": np.float64(0.5), "b": np.int64(3), "c": np.float32(0.25), "d": np.bool_(True)}) == \
        b'{"a":0.5,"b":3,"c":0.25,"d":true}'


def test_rejects_non_finite_and_unsafe_numbers():
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(CanonicalizationError):
            jcs({"x": bad})
    with pytest.raises(CanonicalizationError):
        jcs({"x": 2**60})  # not representable as an IEEE double without loss
    with pytest.raises(CanonicalizationError):
        jcs({"x": object()})


def test_one_digit_edit_changes_commitment():
    edited = json.loads(json.dumps(DOC))
    edited["report"]["productionTonnes"] = 5001
    assert commitment_of(jcs(edited)) != commitment_of(jcs(DOC))


def test_to_jsonable_keeps_bool_distinct_from_int():
    assert to_jsonable({"t": True, "i": 1}) == {"t": True, "i": 1}
    assert jcs({"t": True, "i": 1}) == b'{"i":1,"t":true}'
