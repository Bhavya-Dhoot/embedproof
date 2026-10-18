"""Fail-closed behaviour: /attest refuses (503) unless registry.attestorPqKey(attestor) == keccak256(own ML-DSA pk)."""
import pytest
from eth_utils import keccak
from fastapi.testclient import TestClient

from attestor.app import create_app

from .conftest import ADDR, HONEST_REPORT, KEYS, export_sign, report


class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def mono(attestor):
    attestor._monotonic = Mono()
    return attestor._monotonic


@pytest.fixture
def client(attestor, cfg, mono):
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        yield c


def own_hash(attestor) -> bytes:
    return keccak(attestor.mldsa.public_key)


def attest(client, rep=None):
    rep = rep or HONEST_REPORT
    return client.post("/attest", json={"report": rep, "signature": export_sign(rep, KEYS["exporterA"])})


def test_anchored_service_attests_and_health_says_so(client, attestor):
    assert client.get("/health").json()["pqKeyAnchored"] is True
    assert attest(client).status_code == 200


@pytest.mark.parametrize("registry_value,shown", [
    (bytes(32), "0x" + "00" * 32 + " (unset)"),
    (b"\x07" * 32, "0x" + "07" * 32),
])
def test_unanchored_attest_is_refused_with_a_clear_503(client, attestor, chain, registry_value, shown):
    chain.pq_keys[ADDR["attestor"]] = registry_value
    r = attest(client)
    assert r.status_code == 503
    d = r.json()["detail"]
    assert d.startswith("attestor ML-DSA key not anchored on-chain: registry has " + shown)
    assert "attestor has 0x" + own_hash(attestor).hex() in d and "setAttestorPqKey" in d
    # nothing was signed, stored or sent
    assert chain.batches == {}
    with attestor.store._conn() as c:
        assert c.execute("SELECT count(*) FROM keys").fetchone()[0] == 0
    assert list(attestor.ipfs.root.iterdir()) == []


def test_other_endpoints_stay_available_when_unanchored(client, attestor, chain):
    first = attest(client).json()  # anchored at this point
    chain.pq_keys[ADDR["attestor"]] = bytes(32)
    chain.pq_history[ADDR["attestor"]].clear()  # key revoked
    attestor._anchored_until = 0.0
    h = client.get("/health")
    assert h.status_code == 200 and h.json()["pqKeyAnchored"] is False
    assert client.post("/score", json=HONEST_REPORT).status_code == 200
    v = client.get(f"/verify/{first['cid']}")
    assert v.status_code == 200 and v.json()["pqKeyMatches"] is False and v.json()["allValid"] is False
    assert client.post("/reasons", json={"batchId": 1, "text": "still works"}).status_code == 200


def test_recovers_as_soon_as_the_key_is_anchored_without_restart(client, attestor, chain):
    chain.pq_keys[ADDR["attestor"]] = bytes(32)
    assert attest(client).status_code == 503
    assert attest(client).status_code == 503  # a mismatch is never cached
    chain.pq_keys[ADDR["attestor"]] = own_hash(attestor)  # admin calls setAttestorPqKey
    assert attest(client).status_code == 200


def test_positive_check_is_cached_then_refreshed(client, attestor, chain, mono, monkeypatch):
    reads = []
    real = chain.attestor_pq_key
    monkeypatch.setattr(chain, "attestor_pq_key", lambda a: reads.append(a) or real(a))
    assert attest(client).status_code == 200
    assert attest(client, report(period="2026-10")).status_code == 200
    assert client.get("/health").json()["pqKeyAnchored"] is True
    assert len(reads) == 1  # one chain read serves everything inside the TTL
    chain.pq_keys[ADDR["attestor"]] = b"\x09" * 32  # anchor changed on-chain
    assert attest(client, report(period="2026-11")).status_code == 200  # still inside the TTL
    mono.t += attestor.PQ_ANCHOR_TTL + 1
    r = attest(client, report(period="2026-12"))
    assert r.status_code == 503 and len(reads) == 2  # refreshed after the TTL and caught the change


def test_registry_without_attestor_pq_key_function_is_refused(client, chain):
    chain.pq_supported = False
    r = attest(client)
    assert r.status_code == 503 and "no attestorPqKey function" in r.json()["detail"]


def test_unreadable_registry_is_refused_not_trusted(client, chain, monkeypatch):
    from attestor.chain import ChainError

    def down(a):
        raise ChainError("RPC unreachable")

    monkeypatch.setattr(chain, "attestor_pq_key", down)
    r = attest(client)
    assert r.status_code == 503 and "cannot read registry.attestorPqKey" in r.json()["detail"]
    assert client.get("/health").json()["pqKeyAnchored"] is False


def test_invalid_requests_still_get_their_own_errors_when_anchored(client):
    assert attest(client, report(route="BF-BOF")).status_code == 422
