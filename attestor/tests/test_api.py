"""HTTP-level flow against a fake chain: attest -> verify -> tamper -> disclosure -> reasons."""
import copy
import dataclasses
import hashlib
import json
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from attestor.app import create_app
from attestor.bundle import b64url_decode, cid_v1_raw, digest_from_cid, open_bundle, parse_bundle
from attestor.canonical import keccak256

from .conftest import ADDR, HONEST_REPORT, KEYS, export_sign, report

# FakeScorer and the service share this fixed clock so disclosure timestamps are deterministic.
NOW = 1_790_000_000


@pytest.fixture
def client(attestor, cfg):
    attestor._clock = lambda: NOW
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        yield c


def attest(client, rep=None, key="exporterA"):
    rep = rep or HONEST_REPORT
    return client.post("/attest", json={"report": rep, "signature": export_sign(rep, KEYS[key])})


def disclose(client, batch, key, ts=NOW, msg=None):
    msg = msg or f"EmbedProof disclosure\nbatch:{batch}\nts:{ts}"
    sig = "0x" + Account.sign_message(encode_defunct(text=msg), KEYS[key]).signature.hex()
    return client.post(f"/disclosure/{batch}", json={"address": ADDR[key], "message": msg, "signature": sig})


def test_health(client, attestor):
    h = client.get("/health").json()
    assert h["ok"] is True and h["chainId"] == 31337 and h["attestor"] == ADDR["attestor"]
    assert h["pqBackend"] in ("pqclean", "liboqs", "dilithium-py-reference") and h["ipfsMode"] == "local"
    assert h["modelVersionHash"].startswith("0x")
    assert h["pqPublicKeyHash"] == "0x" + keccak256(attestor.mldsa.public_key).hex()


def test_score_endpoint_and_validation(client):
    assert client.post("/score", json=HONEST_REPORT).json()["band"] == "green"
    r = client.post("/score", json=report(route="BF-BOF"))
    assert r.status_code == 422 and "registry route" in r.json()["detail"]
    assert client.post("/score", json=[1]).status_code == 422


def test_attest_end_to_end(client, attestor):
    r = attest(client)
    assert r.status_code == 200, r.text
    res = r.json()
    assert "Server-Timing" in r.headers and "tx;dur" in r.headers["Server-Timing"]
    assert set(res) == {"batchId", "txHash", "status", "riskScore", "band", "cid", "commitment", "pqSigHash",
                        "ecdsaSignature", "keyB64url", "verifyPath", "score", "gasUsed"}
    assert res["batchId"] == 1 and res["status"] == "Attested" and res["band"] == "green"
    assert res["verifyPath"] == f"/verify/1#k={res['keyB64url']}"

    # the bundle served by the gateway hashes to the CID and to the on-chain digest
    bundle = client.get(f"/bundle/{res['cid']}")
    assert bundle.headers["content-type"].startswith("application/json")
    assert cid_v1_raw(bundle.content) == res["cid"]
    onchain = attestor.chain.batch(1)
    assert hashlib.sha256(bundle.content).digest() == onchain["cidDigest"] == digest_from_cid(res["cid"])
    assert onchain["commitment"].hex() == res["commitment"][2:] and onchain["attestor"] == ADDR["attestor"]
    assert onchain["seeDeclared"] == 4190 and onchain["verifiedTonnes"] == 5000 and onchain["algId"] == 1

    # an independent client with only bundle + key reproduces the commitment and PQ hash
    parsed = parse_bundle(bundle.content)
    canonical = open_bundle(parsed, b64url_decode(res["keyB64url"]))
    assert keccak256(canonical) == parsed["commitment"] == onchain["commitment"]
    assert keccak256(parsed["mldsaSignature"]).hex() == res["pqSigHash"][2:] == onchain["pqSigHash"].hex()
    doc = json.loads(canonical)
    assert doc["schema"] == "embedproof.attestation.v1" and doc["report"] == HONEST_REPORT
    assert doc["chainId"] == 31337 and doc["attestationContract"] == attestor.chain.attestation_address
    assert doc["salt"].startswith("0x") and len(doc["salt"]) == 66 and doc["issuedAt"].endswith("Z")
    assert "latencyMs" not in doc["assessment"] and doc["assessment"]["riskScore"] == 4


def test_verify_untampered_and_tampered(client):
    res = attest(client).json()
    ok = client.get(f"/verify/{res['cid']}").json()
    assert ok["allValid"] is True and ok["batchId"] == 1
    assert ok["commitmentMatches"] and ok["ecdsaValid"] and ok["mldsaValid"] and ok["pqSigHashMatches"] \
        and ok["cidDigestMatches"] and ok["fieldsMatch"] and ok["pqKeyMatches"]
    assert ok["commitmentOnChain"] == ok["commitmentRecomputed"] == res["commitment"]
    assert ok["ecdsaSigner"] == ADDR["attestor"]

    doc = disclose(client, 1, "exporterA").json()["document"]
    assert client.post("/verify", json={"cid": res["cid"], "document": doc}).json()["allValid"] is True

    tampered = copy.deepcopy(doc)
    tampered["report"]["productionTonnes"] = 5001  # a one-digit edit
    bad = client.post("/verify", json={"cid": res["cid"], "document": tampered}).json()
    assert bad["commitmentMatches"] is False and bad["ecdsaValid"] is False and bad["mldsaValid"] is False
    assert bad["allValid"] is False and bad["commitmentRecomputed"] != bad["commitmentOnChain"]
    assert bad["ecdsaSigner"] != ADDR["attestor"]
    # these concern the bundle bytes / key, which the edit does not touch
    assert bad["pqSigHashMatches"] is True and bad["cidDigestMatches"] is True and bad["pqKeyMatches"] is True

    for path, value in [(("assessment", "riskScore"), 5), (("salt",), "0x" + "00" * 32),
                        (("report", "seeTco2PerT"), 0.318)]:
        t = copy.deepcopy(doc)
        node = t
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
        r = client.post("/verify", json={"cid": res["cid"], "document": t}).json()
        assert not r["commitmentMatches"] and not r["ecdsaValid"] and not r["mldsaValid"], path


def test_verify_detects_onchain_field_disagreement(client, attestor):
    res = attest(client).json()
    attestor.chain.batches[1]["seeDeclared"] = 1000  # attestor wrote a different number on-chain than it committed
    r = client.get(f"/verify/{res['cid']}").json()
    assert r["commitmentMatches"] and r["ecdsaValid"] and r["mldsaValid"]
    assert r["fieldsMatch"] is False and r["fieldMismatches"] == ["seeDeclared"] and r["allValid"] is False


def test_verify_detects_swapped_pq_signature_hash(client, attestor):
    res = attest(client).json()
    attestor.chain.batches[1]["pqSigHash"] = b"\x01" * 32
    r = client.get(f"/verify/{res['cid']}").json()
    assert r["pqSigHashMatches"] is False and r["allValid"] is False


def test_verify_errors(client):
    assert client.get("/verify/not-a-cid").status_code == 422
    assert client.get("/verify/" + cid_v1_raw(b"nothing here")).status_code == 404
    assert client.get("/bundle/..%2F..%2Fetc").status_code in (404, 422)
    res = attest(client).json()
    assert client.post("/verify", json={"cid": res["cid"], "document": []}).status_code == 422
    nan_body = ('{"cid": "%s", "document": {"x": NaN}}' % res["cid"]).encode()
    assert client.post("/verify", content=nan_body, headers={"content-type": "application/json"}).status_code == 422


def test_attest_rejects_wrong_signer_and_bad_signature(client):
    r = attest(client, key="exporterB")
    assert r.status_code == 403 and "registered operator" in r.json()["detail"]
    r = client.post("/attest", json={"report": HONEST_REPORT, "signature": "0x1234"})
    assert r.status_code == 422
    # a signature over a different report must not authorise this one
    other = report(productionTonnes=4000)
    r = client.post("/attest", json={"report": HONEST_REPORT, "signature": export_sign(other)})
    assert r.status_code == 403


def test_attest_rejects_invalid_reports(client):
    r = attest(client, report(route="BF-BOF"))
    assert r.status_code == 422 and "registry route" in r.json()["detail"]
    r = attest(client, report(seeTco2PerT=0.2))
    assert r.status_code == 422 and "seeTco2PerT" in r.json()["detail"]
    r = attest(client, report(installationId="TR-XXX-9999"))
    assert r.status_code == 422 and "not registered" in r.json()["detail"]


def test_attest_duplicate_period_conflict_and_next_period_ok(client):
    assert attest(client).status_code == 200
    r = attest(client)
    assert r.status_code == 409 and "DuplicatePeriod" in r.json()["detail"]
    assert attest(client, report(period="2026-10")).status_code == 200


def test_attest_red_report_goes_pending_then_can_be_resubmitted_after_rejection(client, attestor):
    low = report(installationId="TR-BOF-0002", route="BF-BOF", productionTonnes=250000,
                 directEmissionsTco2=100000.0, electricityMwh=50000.0, indirectEmissionsTco2=21000.0,
                 seeTco2PerT=0.484)
    r = attest(client, low, key="exporterB")
    assert r.status_code == 200 and r.json()["status"] == "PendingVerifier" and r.json()["band"] == "red"
    assert attest(client, low, key="exporterB").status_code == 409  # still pending
    attestor.chain.batches[1]["status"] = "Rejected"  # verifier rejected
    assert attest(client, low, key="exporterB").status_code == 200  # contract allows re-attest after rejection


def test_attest_refuses_unapproved_model(client, attestor):
    attestor.chain.approved.clear()
    r = attest(client)
    assert r.status_code == 503 and "not approved" in r.json()["detail"]
    assert attestor.chain.batches == {}


def test_failed_tx_leaves_no_key_behind(client, attestor, monkeypatch):
    from attestor.chain import ChainRevert

    def boom(a, cid):
        raise ChainRevert("EnforcedPause", {})

    monkeypatch.setattr(attestor.chain, "attest", boom)
    r = attest(client)
    assert r.status_code == 409 and r.json()["revert"]["name"] == "EnforcedPause"
    assert r.json()["detail"] == "EnforcedPause()"
    with attestor.store._conn() as c:
        assert c.execute("SELECT count(*) FROM keys").fetchone()[0] == 0


def test_disclosure_authorisation(client, attestor):
    res = attest(client).json()
    # operator, verifier and regulator are allowed
    for who in ("exporterA", "verifier", "regulator"):
        r = disclose(client, 1, who)
        assert r.status_code == 200, (who, r.text)
        assert r.json()["keyB64url"] == res["keyB64url"]
        assert r.json()["document"]["report"] == HONEST_REPORT
    # importer without an allocation, a different operator and the attestor itself are not
    for who in ("importerA", "exporterB", "attestor"):
        assert disclose(client, 1, who).status_code == 403, who
    # importer with an Allocated event on that batch is allowed
    attestor.chain.allocations[1] = {ADDR["importerA"]}
    assert disclose(client, 1, "importerA").status_code == 200
    assert disclose(client, 1, "importerB").status_code == 403


def test_disclosure_message_checks(client):
    attest(client)
    assert disclose(client, 1, "exporterA", ts=NOW - 301).status_code == 403
    assert disclose(client, 1, "exporterA", ts=NOW + 301).status_code == 403
    assert disclose(client, 1, "exporterA", ts=NOW - 300).status_code == 200
    assert disclose(client, 1, "exporterA", msg=f"EmbedProof disclosure\nbatch:2\nts:{NOW}").status_code == 403
    assert disclose(client, 1, "exporterA", msg="hello").status_code == 422
    assert disclose(client, 99, "exporterA").status_code == 404


def test_reasons_round_trip(client):
    attest(client)
    text = "Declared SEE 0.55 is below the BF-BOF plausible interval; rejecting."
    r = client.post("/reasons", json={"batchId": 1, "text": text})
    assert r.status_code == 200
    h = r.json()["reasonHash"]
    assert h == "0x" + keccak256(text.encode("utf-8")).hex()
    assert client.get(f"/reasons/{h}").json() == {"text": text}
    assert client.get(f"/reasons/{h.upper().replace('0X', '0x')}").status_code == 200
    assert client.get("/reasons/0x" + "00" * 32).status_code == 404
    assert client.get("/reasons/zzz").status_code == 422
    assert client.post("/reasons", json={"batchId": 99, "text": "x"}).status_code == 404
    assert client.post("/reasons", json={"batchId": 1, "text": "  "}).status_code == 422
    assert client.post("/reasons", json={"batchId": 1, "text": "x" * 4001}).status_code == 422
    assert client.post("/reasons", json={"batchId": "1", "text": "x"}).status_code == 422  # strict int


def test_rate_limit_on_score_and_attest(attestor, cfg):
    tight = dataclasses.replace(cfg, rate_burst=3, rate_per_sec=0.001)
    with TestClient(create_app(attestor=attestor, cfg=tight)) as c:
        codes = [c.post("/score", json=HONEST_REPORT).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        r = c.post("/score", json=HONEST_REPORT)
        assert r.json()["detail"] == "rate limit exceeded" and int(r.headers["Retry-After"]) >= 1
        # /attest has its own bucket (so a /score flood does not lock out attestations)
        assert c.post("/attest", json={"report": HONEST_REPORT, "signature": export_sign(HONEST_REPORT)}).status_code == 200
        assert c.get("/health").status_code == 200  # health is not limited


def test_rate_limit_uses_trusted_proxy_header(attestor, cfg):
    tight = dataclasses.replace(cfg, rate_burst=1, rate_per_sec=0.001, client_ip_header="fly-client-ip")
    with TestClient(create_app(attestor=attestor, cfg=tight)) as c:
        h = lambda ip: {"Fly-Client-IP": ip}  # noqa: E731
        assert c.post("/score", json=HONEST_REPORT, headers=h("1.1.1.1")).status_code == 200
        assert c.post("/score", json=HONEST_REPORT, headers=h("1.1.1.1")).status_code == 429
        assert c.post("/score", json=HONEST_REPORT, headers=h("2.2.2.2")).status_code == 200


def test_oversized_body_rejected(client):
    r = client.post("/score", content=b"{" + b" " * 70_000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_cors_headers(client):
    r = client.options("/attest", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                                           "Access-Control-Request-Headers": "content-type"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_attest_is_fast(client):
    t0 = time.perf_counter()
    assert attest(client).status_code == 200
    assert time.perf_counter() - t0 < 2.0  # signing + AES + store, no chain latency


def test_pending_tx_keeps_key_so_a_late_mined_record_stays_decryptable(client, attestor, monkeypatch):
    from attestor.chain import TxPending

    def slow(a, cid):
        raise TxPending("no receipt")

    monkeypatch.setattr(attestor.chain, "attest", slow)
    assert attest(client).status_code == 502
    with attestor.store._conn() as c:
        assert c.execute("SELECT count(*) FROM keys").fetchone()[0] == 1
