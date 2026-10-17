"""verify_bundle: `fieldsMatch` binds the on-chain numbers to the signed document (INTERFACES section 6)."""
import copy
import json

import pytest

from attestor.bundle import b64url_decode
from attestor.verify import _doc_mismatches, verify_bundle

from .conftest import ADDR, ATTESTATION_ADDRESS, HONEST_REPORT, MODEL_HASH, export_sign


@pytest.fixture
def attested(attestor, chain):
    res = attestor.attest(HONEST_REPORT, export_sign(HONEST_REPORT))
    return attestor, chain, res


def run(attestor, chain, res, *, batch=None, document=None):
    batch = batch if batch is not None else chain.batch(res["batchId"])
    key = None if document is not None else b64url_decode(res["keyB64url"])
    return verify_bundle(cid=res["cid"], bundle_bytes=attestor.ipfs.get(res["cid"]), batch=batch, mldsa=attestor.mldsa,
                         chain_id=31337, attestation_address=ATTESTATION_ADDRESS,
                         pq_key_on_chain=chain.attestor_pq_key(batch["attestor"]), key=key, document=document)


def test_honest_record_has_matching_fields(attested):
    r = run(*attested)
    assert r["fieldsMatch"] is True and r["fieldMismatches"] == [] and r["allValid"] is True


@pytest.mark.parametrize("field,value,reported", [
    ("installationId", b"TR-BOF-0002".ljust(32, b"\x00"), "installationId"),
    ("period", 202610, "period"),
    ("seeDeclared", 4189, "seeDeclared"),
    ("verifiedTonnes", 5001, "verifiedTonnes"),
    ("riskScore", 5, "riskScore"),
    ("modelVersionHash", b"\x01" * 32, "modelVersionHash"),
])
def test_each_onchain_number_must_agree_with_the_signed_document(attested, field, value, reported):
    attestor, chain, res = attested
    batch = dict(chain.batch(res["batchId"]))
    batch[field] = value  # attestor (or a bug) wrote a different number on-chain than it committed
    r = run(attestor, chain, res, batch=batch)
    assert r["commitmentMatches"] and r["ecdsaValid"] and r["mldsaValid"]  # signatures alone cannot see this
    assert r["fieldsMatch"] is False and r["fieldMismatches"] == [reported] and r["allValid"] is False


@pytest.mark.parametrize("path,value,reported", [
    (("chainId",), 1, "chainId"),
    (("attestationContract",), ADDR["attestor"], "attestationContract"),
])
def test_document_chain_binding_must_match(attested, path, value, reported):
    attestor, chain, res = attested
    doc = json.loads(attestor.store.get_document(res["cid"]))
    doc[path[0]] = value
    r = run(attestor, chain, res, document=doc)
    assert r["fieldsMatch"] is False and reported in r["fieldMismatches"] and r["allValid"] is False


def test_supplied_document_with_edited_report_fails_fields_and_signatures(attested):
    attestor, chain, res = attested
    doc = json.loads(attestor.store.get_document(res["cid"]))
    doc["report"]["productionTonnes"] = 5001
    r = run(attestor, chain, res, document=doc)
    assert not r["commitmentMatches"] and not r["ecdsaValid"] and not r["mldsaValid"]
    assert r["fieldsMatch"] is False and r["fieldMismatches"] == ["verifiedTonnes"]


def test_unedited_supplied_document_passes(attested):
    attestor, chain, res = attested
    doc = json.loads(attestor.store.get_document(res["cid"]))
    assert run(attestor, chain, res, document=doc)["allValid"] is True


def test_see_comparison_uses_round_half_up_not_float_rounding():
    doc = {"chainId": 31337, "attestationContract": ATTESTATION_ADDRESS,
           "report": {**HONEST_REPORT, "seeTco2PerT": 0.41955, "productionTonnes": 5000.9},
           "assessment": {"riskScore": 4, "modelVersionHash": MODEL_HASH}}
    batch = {"installationId": b"TR-EAF-0001".ljust(32, b"\x00"), "period": 202609, "seeDeclared": 4196,
             "verifiedTonnes": 5000, "riskScore": 4, "modelVersionHash": bytes.fromhex(MODEL_HASH[2:])}
    raw = json.dumps(doc).encode()
    assert _doc_mismatches(raw, batch, 31337, ATTESTATION_ADDRESS) == []
    assert _doc_mismatches(raw, {**batch, "seeDeclared": 4195}, 31337, ATTESTATION_ADDRESS) == ["seeDeclared"]


@pytest.mark.parametrize("garbage", [b"not json", b"[]", b'{"report": 1}', b'{"report": {}, "assessment": {}}', b"\xff\xfe"])
def test_structurally_wrong_document_is_a_mismatch_not_a_crash(garbage):
    batch = {"installationId": bytes(32), "period": 0, "seeDeclared": 0, "verifiedTonnes": 0, "riskScore": 0,
             "modelVersionHash": bytes(32)}
    out = _doc_mismatches(garbage, batch, 31337, ATTESTATION_ADDRESS)
    assert out and out[0].startswith("document structure")


def test_different_attestation_contract_case_is_tolerated(attested):
    attestor, chain, res = attested
    doc = json.loads(attestor.store.get_document(res["cid"]))
    assert copy.deepcopy(doc)["attestationContract"] == ATTESTATION_ADDRESS
    assert _doc_mismatches(json.dumps(doc).encode().replace(ATTESTATION_ADDRESS.encode(), ATTESTATION_ADDRESS.lower().encode()),
                           chain.batch(res["batchId"]), 31337, ATTESTATION_ADDRESS) == []


# ---- pqKeyMatches: keccak256(bundle ML-DSA pk) == registry.attestorPqKey(batch.attestor), non-zero


def test_pq_key_anchored_on_chain_matches(attested):
    r = run(*attested)
    assert r["pqKeyMatches"] is True and r["allValid"] is True


@pytest.mark.parametrize("registry_value", [bytes(32), b"\x07" * 32, None], ids=["unset", "other-key", "old-registry"])
def test_pq_key_not_anchored_or_different_fails_everything_else_still_true(attested, registry_value):
    attestor, chain, res = attested
    if registry_value is None:
        chain.pq_supported = False  # registry ABI predates attestorPqKey -> cannot be confirmed
    else:
        chain.pq_keys[ADDR["attestor"]] = registry_value
    r = attestor.verify(res["cid"])
    assert r["pqKeyMatches"] is False and r["allValid"] is False
    assert r["commitmentMatches"] and r["ecdsaValid"] and r["mldsaValid"] and r["pqSigHashMatches"] and r["fieldsMatch"]


def test_pq_key_check_uses_the_key_in_the_bundle_not_the_services_current_key(attested, tmp_path):
    """A bundle signed by a different (e.g. rotated or attacker) ML-DSA key must not satisfy the anchor."""
    from attestor.signing import MLDSASigner
    from attestor.canonical import keccak256

    attestor, chain, res = attested
    attestor.mldsa = MLDSASigner.load_or_create(tmp_path)  # service rotated its key after the batch was made
    chain.pq_keys[ADDR["attestor"]] = keccak256(attestor.mldsa.public_key)  # registry now anchors the NEW key
    r = attestor.verify(res["cid"])
    assert r["pqKeyMatches"] is False and r["allValid"] is False  # old batch still carries the old key
