"""Regression tests for the v1.1 hardening items (INTERFACES section 9.H) and the review findings."""
import asyncio
import dataclasses
import json
import threading
from types import SimpleNamespace

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from fastapi.testclient import TestClient

from attestor.app import create_app
from attestor.chain import Chain, ChainError, ChainRevert
from attestor.ipfs import IpfsError
from attestor.limits import BodyLimitMiddleware, client_key
from attestor.report import ReportError, validate_report
from attestor.verify import _doc_mismatches

from .conftest import ADDR, ATTESTATION_ADDRESS, HONEST_REPORT, KEYS, MODEL_HASH, NOW, export_sign, report

EAF = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 100000, "country": "TR"}
SECRET = "SECRETAPIKEY123"
KEYED_URL = f"https://base-sepolia.g.alchemy.com/v2/{SECRET}"
CID = "bafkreibm6jg3ux5qumhcn2b3flc3tyu6dmlb4xa7u5bf44yegnrjhc4yeq"


@pytest.fixture
def client(attestor, cfg):
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        yield c


def attest(client, rep=None, key="exporterA"):
    rep = rep or HONEST_REPORT
    return client.post("/attest", json={"report": rep, "signature": export_sign(rep, KEYS[key])})


# ------------------------------------------------------------------ streaming body cap


def test_chunked_5mb_body_is_refused_with_413(client):
    def chunks():
        for _ in range(80):
            yield b" " * 65_536  # 5 MiB, no Content-Length (Transfer-Encoding: chunked)

    r = client.post("/score", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}
    r = client.post("/attest", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_body_limit_stops_reading_at_the_cap_and_ignores_a_lying_content_length():
    received, sent = [], []

    async def app(scope, receive, send):  # an app that slurps the whole body, like request.body()
        while True:
            m = await receive()
            received.append(len(m["body"]))
            if not m.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    chunks = [{"type": "http.request", "body": b"x" * 1000, "more_body": True} for _ in range(100)]

    async def receive():
        return chunks.pop(0)

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "headers": [(b"content-length", b"10")]}  # claims 10 bytes, streams 100 kB
    asyncio.run(BodyLimitMiddleware(app, max_bytes=5_000)(scope, receive, send))
    assert sent[0]["status"] == 413
    assert len(received) == 5 and len(chunks) == 94  # the 6th chunk crossed 5 kB and was dropped; 94 never read


def test_body_limit_passes_small_bodies_and_rejects_large_content_length_without_reading():
    sent, reads = [], []

    async def app(scope, receive, send):
        reads.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"abc", "more_body": False}

    async def send(m):
        sent.append(m)

    mw = BodyLimitMiddleware(app, max_bytes=100)
    asyncio.run(mw({"type": "http", "headers": []}, receive, send))
    assert sent[0]["status"] == 200 and reads == [b"abc"]
    sent.clear()
    asyncio.run(mw({"type": "http", "headers": [(b"content-length", b"101")]}, receive, send))
    assert sent[0]["status"] == 413 and len(reads) == 1  # app never ran


# ------------------------------------------------------------------ no leaking of RPC / provider text


def test_chain_error_text_with_api_key_is_not_returned(client, chain, monkeypatch):
    def boom(*a):
        raise ChainError(f"HTTPSConnectionPool(host='{KEYED_URL}'): Max retries exceeded")

    monkeypatch.setattr(chain, "batch_of", boom)
    r = attest(client)
    assert r.status_code == 502 and SECRET not in r.text and "alchemy" not in r.text
    assert r.json()["detail"] == "chain unavailable or misconfigured (see service log)"


def test_real_chain_translate_never_exposes_exception_text_and_decodes_only_from_data():
    chain = Chain.__new__(Chain)
    chain._errors = Chain._build_error_table([[{"type": "error", "name": "DoubleClaimBlocked", "inputs": [
        {"name": "batchId", "type": "uint256"}, {"name": "requested", "type": "uint64"},
        {"name": "remaining", "type": "uint64"}]}]])
    selector = "0x" + keccak(text="DoubleClaimBlocked(uint256,uint64,uint64)")[:4].hex()
    payload = selector + "".join(f"{v:064x}" for v in (7, 5000, 0))

    # keyed RPC error: generic ChainError, secret nowhere in the message
    out = chain.translate(RuntimeError(f"429 from {KEYED_URL}"))
    assert type(out) is ChainError and SECRET not in str(out)

    # revert data that only appears in node-controlled message text is NOT parsed
    assert type(chain.translate(RuntimeError(f"execution reverted: custom error {payload}"))) is ChainError
    assert type(chain.translate(RuntimeError(payload))) is ChainError

    class FromWeb3(Exception):  # shape of web3's ContractCustomError(message, data)
        data = payload

    decoded = chain.translate(FromWeb3("ignored"))
    assert isinstance(decoded, ChainRevert) and decoded.name == "DoubleClaimBlocked"
    assert decoded.revert_args == {"batchId": 7, "requested": 5000, "remaining": 0}


def test_pq_anchor_scorer_and_ipfs_errors_do_not_leak(client, attestor, chain, monkeypatch):
    def down(a):
        raise ChainError(f"cannot reach {KEYED_URL}")

    with monkeypatch.context() as m:
        m.setattr(chain, "attestor_pq_key", down)
        r = attest(client)
        assert r.status_code == 503 and SECRET not in r.text
    attestor._anchored_until = 0.0

    def bad_scorer(*a):
        raise RuntimeError(KEYED_URL)

    with monkeypatch.context() as m:
        m.setattr(attestor.scorer, "score_report", bad_scorer)
        r = client.post("/score", json=HONEST_REPORT)
        assert r.status_code == 500 and SECRET not in r.text

    def bad_ipfs(b):
        raise IpfsError(f"Pinata said {KEYED_URL}")

    with monkeypatch.context() as m:
        m.setattr(attestor.ipfs, "put", bad_ipfs)
        r = attest(client)
        assert r.status_code == 502 and SECRET not in r.text and "storage failed" in r.json()["detail"]


# ------------------------------------------------------------------ numbers and periods


HUGE = 10 ** 400  # 401 digits: math.isfinite(HUGE) raises OverflowError


def test_401_digit_integer_is_a_422_not_a_500(client):
    body = json.dumps({**HONEST_REPORT, "productionTonnes": HUGE})
    r = client.post("/score", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422 and "within +-(2^53-1)" in r.json()["detail"]
    raw = ('{"report": ' + body + ', "signature": "0x' + "11" * 65 + '"}').encode()
    assert client.post("/attest", content=raw, headers={"content-type": "application/json"}).status_code == 422
    for field in ("directEmissionsTco2", "electricityMwh", "gridFactorTco2PerMwh", "indirectEmissionsTco2", "seeTco2PerT"):
        with pytest.raises(ReportError):
            validate_report({**HONEST_REPORT, field: HUGE}, EAF)
        with pytest.raises(ReportError):
            validate_report({**HONEST_REPORT, field: -HUGE}, EAF)
    with pytest.raises(ReportError):
        validate_report({**HONEST_REPORT, "productionTonnes": 2 ** 53}, EAF)


def test_future_period_is_rejected_but_the_current_month_is_fine(client):
    # the service clock is conftest.NOW = 2027-05
    r = client.post("/score", json=report(period="2027-06"))
    assert r.status_code == 422 and "in the future" in r.json()["detail"]
    assert client.post("/score", json=report(period="2099-01")).status_code == 422
    assert client.post("/score", json=report(period="2027-05")).status_code == 200
    assert client.post("/score", json=report(period="2020-01")).status_code == 200
    r = attest(client, report(period="2027-06"))
    assert r.status_code == 422 and "in the future" in r.json()["detail"]


# ------------------------------------------------------------------ strict-typed fieldsMatch


BATCH = {"installationId": b"TR-EAF-0001".ljust(32, b"\x00"), "period": 202609, "seeDeclared": 4190,
         "verifiedTonnes": 5000, "riskScore": 4, "modelVersionHash": bytes.fromhex(MODEL_HASH[2:])}


def doc(**over):
    d = {"chainId": 31337, "attestationContract": ATTESTATION_ADDRESS, "report": dict(HONEST_REPORT),
         "assessment": {"riskScore": 4, "modelVersionHash": MODEL_HASH}}
    for k, v in over.items():
        target = d["assessment"] if k in ("riskScore", "modelVersionHash") else d["report"] if k in HONEST_REPORT else d
        target[k] = v
    return json.dumps(d).encode()


def mismatches(raw, batch=BATCH, chain_id=31337):
    return _doc_mismatches(raw, batch, chain_id, ATTESTATION_ADDRESS)


def test_strict_fields_baseline_matches():
    assert mismatches(doc()) == []


@pytest.mark.parametrize("risk", [4.9, 4.0, "4", True, None, [4]])
def test_risk_score_must_be_an_exact_integer(risk):
    assert mismatches(doc(riskScore=risk)) == ["riskScore"]
    # even where a lenient int() coercion would equal the batch's value
    batch = {**BATCH, "riskScore": 1 if risk is True else 4}
    assert mismatches(doc(riskScore=risk), batch) == ["riskScore"]


def test_bool_chain_id_is_not_the_integer_one():
    assert mismatches(doc(chainId=True), chain_id=1) == ["chainId"]
    assert mismatches(doc(chainId=1), chain_id=1) == []
    assert mismatches(doc(chainId=31337.0)) == ["chainId"]
    assert mismatches(doc(chainId="31337")) == ["chainId"]


@pytest.mark.parametrize("field,value", [
    ("period", "2026-09\n"), ("period", 202609), ("period", "2026-9"), ("installationId", "TR-EAF-0001\n"),
    ("installationId", 5), ("seeTco2PerT", "0.419"), ("seeTco2PerT", True), ("productionTonnes", "5000"),
    ("productionTonnes", True), ("attestationContract", ATTESTATION_ADDRESS + "\n"),
    ("modelVersionHash", MODEL_HASH.upper()), ("modelVersionHash", MODEL_HASH[2:]),
])
def test_other_fields_are_strictly_typed_and_formatted(field, value):
    assert mismatches(doc(**{field: value})) != []


# ------------------------------------------------------------------ operator role (9.B)


def test_attest_requires_operator_role_not_just_registry_operator(client, chain):
    chain.roles.discard(("OPERATOR_ROLE", ADDR["exporterA"]))
    r = attest(client)
    assert r.status_code == 403 and "OPERATOR_ROLE" in r.json()["detail"]
    assert chain.batches == {}
    chain.roles.add(("OPERATOR_ROLE", ADDR["exporterA"]))
    assert attest(client).status_code == 200


# ------------------------------------------------------------------ rate limits everywhere + IPv6 /64


def test_client_key_buckets_ipv6_by_slash_64():
    a = client_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    assert a == client_key("2001:db8:1:2:1111:2222:3333:4444") == "2001:db8:1:2::/64"
    assert client_key("2001:db8:1:3::1") != a
    assert client_key("::ffff:203.0.113.9") == client_key("203.0.113.9") == "203.0.113.9"
    assert client_key("203.0.113.9") != client_key("203.0.113.10")
    assert client_key("not an ip") == "not an ip"
    assert client_key("") == "unknown"
    assert len(client_key("x" * 500)) <= 64


def test_ipv6_rotation_inside_a_prefix_shares_one_bucket(attestor, cfg):
    tight = dataclasses.replace(cfg, rate_burst=2, rate_per_sec=0.001, client_ip_header="x-real-ip")
    with TestClient(create_app(attestor=attestor, cfg=tight)) as c:
        codes = [c.post("/score", json=HONEST_REPORT, headers={"x-real-ip": f"2001:db8:9:9::{i:x}"}).status_code
                 for i in range(1, 5)]
        assert codes == [200, 200, 429, 429]
        assert c.post("/score", json=HONEST_REPORT, headers={"x-real-ip": "2001:db8:9:a::1"}).status_code == 200


@pytest.mark.parametrize("method,path,json_body", [
    ("post", "/disclosure/1", {"address": ADDR["exporterA"], "message": "x", "signature": "0x"}),
    ("get", "/verify/" + CID, None),
    ("post", "/verify", {"cid": CID}),
    ("get", "/bundle/" + CID, None),
    ("get", "/reasons/0x" + "00" * 32, None),
    ("post", "/reasons", {"batchId": 1, "text": "x"}),
])
def test_every_data_endpoint_is_rate_limited(attestor, cfg, method, path, json_body):
    tight = dataclasses.replace(cfg, rate_burst=2, rate_per_sec=0.001)
    with TestClient(create_app(attestor=attestor, cfg=tight)) as c:
        def call():
            kwargs = {"json": json_body} if json_body is not None else {}
            return getattr(c, method)(path, **kwargs).status_code

        codes = [call() for _ in range(4)]
        assert codes[2:] == [429, 429] and 429 not in codes[:2], codes
        assert c.get("/health").status_code == 200  # /health stays unthrottled


# ------------------------------------------------------------------ importers_of cache


class FakeAllocated:
    def __init__(self, store):
        self.store, self.calls = store, []

    def Allocated(self):  # noqa: N802  (web3 API shape)
        return self

    def get_logs(self, argument_filters, from_block, to_block):
        self.calls.append((from_block, to_block))
        return [{"args": {"importer": imp}} for blk, imp in self.store if from_block <= blk <= to_block]


def make_chain(events, latest_ref, deploy_block=10):
    class Eth:
        @property
        def block_number(self):
            return latest_ref[0]

    c = Chain.__new__(Chain)
    c.deploy_block, c._importers, c._importers_lock = deploy_block, {}, threading.Lock()
    c.w3 = SimpleNamespace(eth=Eth())
    c.allocator = SimpleNamespace(events=events)
    return c


def test_importers_cache_is_incremental_and_never_hides_a_new_allocation():
    store, latest = [(12, ADDR["importerA"].lower())], [20]
    events = FakeAllocated(store)
    chain = make_chain(events, latest)
    assert chain.importers_of(1) == {ADDR["importerA"]}
    assert events.calls == [(10, 20)]
    assert chain.importers_of(1) == {ADDR["importerA"]} and events.calls == [(10, 20)]  # no new blocks -> no RPC
    store.append((25, ADDR["importerB"].lower()))
    latest[0] = 30
    assert chain.importers_of(1) == {ADDR["importerA"], ADDR["importerB"]}
    assert events.calls[-1] == (21, 30)  # only the new blocks were scanned
    assert chain.importers_of(2) == {ADDR["importerA"], ADDR["importerB"]}  # separate batch, separate cache entry
    assert events.calls[-1] == (10, 30)


def test_log_scans_are_windowed_for_public_rpc_range_limits():
    events = FakeAllocated([(12_000, ADDR["importerA"].lower())])
    chain = make_chain(events, [12_500], deploy_block=0)
    assert chain.importers_of(1) == {ADDR["importerA"]}
    assert events.calls == [(0, 4_999), (5_000, 9_999), (10_000, 12_500)]


# ------------------------------------------------------------------ address and hash strictness


def bad_addresses():
    a = ADDR["exporterA"]
    return [a[2:], a + chr(10), " " + a, "0X" + a[2:], "0x" + a[2:-1] + chr(0x663), "0x" + a[2:-1]]


@pytest.mark.parametrize("addr", bad_addresses())
def test_disclosure_address_must_be_0x_plus_40_ascii_hex(client, addr):
    msg = f"EmbedProof disclosure\nbatch:1\nts:{NOW}"
    sig = "0x" + Account.sign_message(encode_defunct(text=msg), KEYS["exporterA"]).signature.hex()
    r = client.post("/disclosure/1", json={"address": addr, "message": msg, "signature": sig})
    assert r.status_code == 422 and "valid EVM address" in r.json()["detail"]


def test_reason_hash_path_with_trailing_newline_is_rejected(client):
    attest(client)
    h = client.post("/reasons", json={"batchId": 1, "text": "ok"}).json()["reasonHash"]
    assert client.get(f"/reasons/{h}").status_code == 200
    assert client.get(f"/reasons/{h}%0A").status_code == 422
