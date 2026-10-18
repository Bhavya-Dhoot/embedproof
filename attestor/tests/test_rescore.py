"""python -m attestor.rescore: re-run the frozen model on a certificate and compare with the chain (round 3 item 4)."""
import json
import shutil
from types import SimpleNamespace

import pytest

from attestor import rescore
from attestor.rescore import RescoreError, installations_from_inputs, parse_certificate_url, run_checks

from .conftest import ARTEFACT, HONEST_REPORT, export_sign

KEY = bytes(range(32))
KEY_B64U = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


# ------------------------------------------------------------------ URL parsing


@pytest.mark.parametrize("url", [f"http://localhost:3000/verify/12#k={KEY_B64U}", f"https://x.example/verify/12/#k={KEY_B64U}",
                                 f"/verify/12#k={KEY_B64U}", f"  http://h/app/verify/12#k={KEY_B64U}&other=1  "])
def test_parse_certificate_url(url):
    assert parse_certificate_url(url) == (12, KEY)


@pytest.mark.parametrize("url", ["http://h/verify/abc#k=" + KEY_B64U, "http://h/verify/12", "http://h/verify/12#x=1",
                                 "http://h/verify/12#k=" + KEY_B64U[:-3], "http://h/other/12#k=" + KEY_B64U,
                                 "http://h/verify/12#k=%%%", "http://h/verify/-1#k=" + KEY_B64U, ""])
def test_parse_certificate_url_rejects(url):
    with pytest.raises(RescoreError):
        parse_certificate_url(url)


def test_key_travels_in_the_fragment_not_the_query():
    with pytest.raises(RescoreError):
        parse_certificate_url(f"http://h/verify/12?k={KEY_B64U}")


# ------------------------------------------------------------------ the checks, with the real model


@pytest.fixture
def green(real_attestor, chain):
    res = real_attestor.attest(HONEST_REPORT, export_sign(HONEST_REPORT))
    key = rescore.b64url_decode(res["keyB64url"])
    return SimpleNamespace(att=real_attestor, chain=chain, res=res, key=key, batch_id=res["batchId"],
                           fetch=real_attestor.ipfs.get)


def checks_by_letter(checks):
    return {c.label[1]: c for c in checks}


def test_green_certificate_rescores_and_all_checks_pass(green):
    checks, ctx = run_checks(green.batch_id, green.key, green.chain, green.fetch, ARTEFACT)
    by = checks_by_letter(checks)
    assert sorted(by) == ["a", "b", "c", "d", "e"] and all(c.ok for c in checks), [(c.label, c.detail) for c in checks]
    assert ctx["cid"] == green.res["cid"]
    assert by["b"].detail == f"recomputed {green.res['riskScore']}, on-chain {green.res['riskScore']}"


def test_rescore_ignores_the_installations_file_and_uses_only_the_recorded_inputs(green, tmp_path, monkeypatch):
    """Even with a different INSTALLATIONS_FILE the recorded inputs reproduce the signed score."""
    doc = green.att.store.get_document(green.res["cid"])
    document = json.loads(doc)
    assert document["assessment"]["inputs"]["features"]["ctLaggedPriorEf"] == 0.08
    scored = rescore.rescore_document(document, ARTEFACT)
    assert scored["riskScore"] == document["assessment"]["riskScore"] == green.res["riskScore"]
    assert installations_from_inputs(document["assessment"]["inputs"]) == {
        "TR-EAF-0001": {"country": "TR", "priors": {"capacityTpy": 300000, "gridGco2PerKwh": 475.73, "ctLaggedPriorEf": 0.08}}}


def test_wrong_key_is_a_retrieval_error_not_a_pass(green):
    with pytest.raises(RescoreError, match="cannot decrypt"):
        run_checks(green.batch_id, bytes(32), green.chain, green.fetch, ARTEFACT)


def test_unknown_batch_and_unfetchable_bundle(green):
    with pytest.raises(RescoreError, match="does not exist"):
        run_checks(99, green.key, green.chain, green.fetch, ARTEFACT)
    with pytest.raises(RescoreError, match="could not be fetched"):
        run_checks(green.batch_id, green.key, green.chain, lambda cid: None, ARTEFACT)


def test_a_modified_model_artefact_fails_check_a(green, tmp_path):
    other = tmp_path / "artefact.json"
    shutil.copy(ARTEFACT, other)
    other.write_bytes(other.read_bytes() + b"\n")  # same model, different file bytes -> different hash
    by = checks_by_letter(run_checks(green.batch_id, green.key, green.chain, green.fetch, other)[0])
    assert by["a"].ok is False and "vs chain" in by["a"].detail
    assert by["b"].ok and by["c"].ok


def test_a_different_onchain_risk_score_fails_check_b(green):
    green.chain.batches[green.batch_id]["riskScore"] = green.res["riskScore"] + 7
    by = checks_by_letter(run_checks(green.batch_id, green.key, green.chain, green.fetch, ARTEFACT)[0])
    assert by["b"].ok is False and by["a"].ok and by["c"].ok


def test_a_different_onchain_commitment_fails_check_c(green):
    green.chain.batches[green.batch_id]["commitment"] = b"\x01" * 32
    by = checks_by_letter(run_checks(green.batch_id, green.key, green.chain, green.fetch, ARTEFACT)[0])
    assert by["c"].ok is False and by["a"].ok and by["b"].ok


def test_a_different_onchain_cid_digest_fails_check_d(green):
    other_digest = b"\x02" * 32
    green.chain.batches[green.batch_id]["cidDigest"] = other_digest
    checks, _ = run_checks(green.batch_id, green.key, green.chain, lambda cid: green.fetch(green.res["cid"]), ARTEFACT)
    assert checks_by_letter(checks)["d"].ok is False


def test_certificate_without_recorded_inputs_cannot_be_rescored(attestor, chain):
    """A pre-round-3 style attestation (no assessment.inputs) is a FAIL with a clear reason, never a silent pass."""
    attestor.assessor = None
    res = attestor.attest(HONEST_REPORT, export_sign(HONEST_REPORT))
    checks, _ = run_checks(res["batchId"], rescore.b64url_decode(res["keyB64url"]), chain, attestor.ipfs.get, ARTEFACT)
    by = checks_by_letter(checks)
    assert by["b"].ok is False and "assessment.inputs" in by["b"].detail
    assert by["a"].ok is False  # the test double's model hash is not the artefact's


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def cli(green, monkeypatch, capsys):
    import attestor.chain as chain_mod

    monkeypatch.setattr(chain_mod, "Chain", lambda cfg: green.chain)
    monkeypatch.setattr(rescore, "httpx", SimpleNamespace(
        get=lambda url, timeout=30: SimpleNamespace(status_code=200, content=green.fetch(url.rsplit("/", 1)[1])),
        HTTPError=Exception))
    green.chain.chain_id = 31337

    def run(url=None, *extra):
        url = url or f"http://localhost:3000{green.res['verifyPath']}"
        code = rescore.main([url, "--attestor", "http://attestor.test", *extra])
        return code, capsys.readouterr()

    return run


def test_cli_prints_pass_lines_and_exits_zero(cli, green):
    code, out = cli()
    assert code == 0, out.out
    lines = out.out.splitlines()
    assert lines[0].startswith(f"rescore batch {green.batch_id}  cid {green.res['cid']}")
    assert [ln.split()[0] for ln in lines[1:-1]] == ["PASS"] * 5
    assert "(a) sha256(model artefact file) == on-chain modelVersionHash" in lines[1]
    assert "(b) recomputed risk score == on-chain riskScore" in lines[2]
    assert "(c) keccak256(JCS(document)) == on-chain commitment" in lines[3]
    assert lines[-1] == "RESCORE PASS"


def test_cli_exits_one_on_any_fail(cli, green):
    green.chain.batches[green.batch_id]["riskScore"] += 1
    code, out = cli()
    assert code == 1 and "FAIL  (b)" in out.out and out.out.splitlines()[-1].startswith("RESCORE FAIL (1 of 5 checks)")


def test_cli_exits_two_on_usage_or_retrieval_problems(cli, green):
    assert cli("http://h/not-a-certificate")[0] == 2
    code, out = cli(f"http://h/verify/{green.batch_id}#k={KEY_B64U}")
    assert code == 2 and "cannot decrypt" in out.err
    assert cli(f"http://h/verify/77#k={KEY_B64U}")[0] == 2
