"""Pinata path of the IPFS store with httpx.MockTransport (no network): success, CID mismatch and failures (round 3 item 7)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from attestor.app import create_app
from attestor.bundle import BundleError, cid_v1_raw
from attestor.ipfs import PINATA_UPLOAD_URL, IpfsError, IpfsStore

from .conftest import HONEST_REPORT, export_sign

JWT = "test-jwt-not-a-real-token"
DATA = b'{"schema":"embedproof.bundle.v1","note":"bytes pinned to IPFS"}'


class Pinata:
    """Records requests and answers like the v3 files API: {"data": {"cid": ...}}."""

    def __init__(self, respond):
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def ok_with(cid):
    return lambda request: httpx.Response(200, json={"data": {"id": "file-id", "name": "n", "cid": cid, "size": 1}})


def store(tmp_path, pinata) -> IpfsStore:
    return IpfsStore(tmp_path / "ipfs", pinata_jwt=JWT, http=pinata.client())


def test_success_uploads_the_exact_bytes_with_the_bearer_token_and_returns_the_local_cid(tmp_path):
    cid = cid_v1_raw(DATA)
    pinata = Pinata(ok_with(cid))
    s = store(tmp_path, pinata)
    assert s.mode == "local+pinata"
    assert s.put(DATA) == cid
    assert len(pinata.requests) == 1
    req = pinata.requests[0]
    assert req.method == "POST" and str(req.url) == PINATA_UPLOAD_URL == "https://uploads.pinata.cloud/v3/files"
    assert req.headers["authorization"] == f"Bearer {JWT}"
    assert req.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = req.read()
    assert DATA in body  # the file part carries the bundle bytes verbatim
    assert b'name="network"' in body and b"public" in body
    assert f'filename="{cid}.json"'.encode() in body and b"application/json" in body
    # local content-addressed copy is written too and is readable back (re-hashed against the CID)
    assert (tmp_path / "ipfs" / cid).read_bytes() == DATA and s.get(cid) == DATA


def test_success_is_idempotent_for_identical_bytes(tmp_path):
    cid = cid_v1_raw(DATA)
    pinata = Pinata(ok_with(cid))
    s = store(tmp_path, pinata)
    assert s.put(DATA) == s.put(DATA) == cid
    assert len(pinata.requests) == 2  # pinning is re-asserted; the local file is not rewritten
    assert list((tmp_path / "ipfs").iterdir()) == [tmp_path / "ipfs" / cid]


def test_cid_mismatch_is_refused_with_both_cids_in_the_error(tmp_path):
    local = cid_v1_raw(DATA)
    other = cid_v1_raw(b"a different file")  # e.g. a dag-pb wrapped CID or a re-chunked add would differ like this
    s = store(tmp_path, Pinata(ok_with(other)))
    with pytest.raises(IpfsError) as exc:
        s.put(DATA)
    msg = str(exc.value)
    assert other in msg and local in msg and "refusing to attest" in msg and "cidDigest" in msg


def test_dag_pb_cid_from_pinata_is_a_mismatch(tmp_path):
    dag_pb = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    with pytest.raises(IpfsError, match="differs from the locally computed"):
        store(tmp_path, Pinata(ok_with(dag_pb))).put(DATA)


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"error": "invalid token"}),
    httpx.Response(500, text="internal error"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"unexpected": "shape"}),
    httpx.Response(200, json={"data": {}}),
])
def test_http_errors_and_malformed_responses_raise_ipfs_error(tmp_path, response):
    s = store(tmp_path, Pinata(lambda request: response))
    with pytest.raises(IpfsError):
        s.put(DATA)


def test_network_failure_raises_ipfs_error(tmp_path):
    def boom(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(IpfsError, match="Pinata upload failed"):
        store(tmp_path, Pinata(boom)).put(DATA)


def test_without_a_jwt_nothing_is_sent(tmp_path):
    pinata = Pinata(ok_with("unused"))
    s = IpfsStore(tmp_path / "ipfs", pinata_jwt=None, http=pinata.client())
    assert s.mode == "local" and s.put(DATA) == cid_v1_raw(DATA) and pinata.requests == []


def test_gateway_fallback_returns_only_bytes_that_hash_to_the_cid(tmp_path):
    cid = cid_v1_raw(DATA)

    def gateway(request):
        assert str(request.url) == f"https://gw.example/ipfs/{cid}"
        return httpx.Response(200, content=DATA)

    s = IpfsStore(tmp_path / "ipfs", gateway="https://gw.example", http=httpx.Client(transport=httpx.MockTransport(gateway)))
    assert s.get(cid) == DATA
    evil = IpfsStore(tmp_path / "ipfs2", gateway="https://gw.example",
                     http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"swapped"))))
    with pytest.raises(BundleError, match="do not hash to the CID"):
        evil.get(cid)


# ------------------------------------------------------------------ through the service


def test_attest_pins_via_pinata_and_the_pinned_bytes_are_the_attested_ones(attestor, cfg):
    captured = {}

    def handler(request):
        body = request.read()
        captured["body"] = body
        # echo the CID of whatever file part arrived, like a well-behaved Pinata
        start = body.index(b'{"schema"')
        file_bytes = body[start:body.index(b"\r\n--", start)]
        captured["file"] = file_bytes
        return httpx.Response(200, json={"data": {"cid": cid_v1_raw(file_bytes)}})

    attestor.ipfs = IpfsStore(cfg.ipfs_dir, pinata_jwt=JWT, http=httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        r = c.post("/attest", json={"report": HONEST_REPORT, "signature": export_sign(HONEST_REPORT)})
        assert r.status_code == 200, r.text
        res = r.json()
        assert cid_v1_raw(captured["file"]) == res["cid"]  # what Pinata pinned is exactly what the CID names
        assert json.loads(captured["file"])["commitment"] == res["commitment"]
        assert c.get("/health").json()["ipfsMode"] == "local+pinata"


def test_attest_is_refused_before_any_transaction_when_pinata_disagrees_on_the_cid(attestor, cfg):
    wrong = cid_v1_raw(b"not our bundle")
    attestor.ipfs = IpfsStore(cfg.ipfs_dir, pinata_jwt=JWT, http=Pinata(ok_with(wrong)).client())
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        r = c.post("/attest", json={"report": HONEST_REPORT, "signature": export_sign(HONEST_REPORT)})
    assert r.status_code == 502 and r.json() == {"detail": "bundle storage failed (see service log)"}
    assert wrong not in r.text  # provider text never reaches the client
    assert attestor.chain.batches == {}  # no transaction was sent
    with attestor.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM keys").fetchone()[0] == 0  # nothing half-stored
