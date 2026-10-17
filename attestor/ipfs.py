"""Content-addressed bundle store: local directory, optionally mirrored to Pinata (v3 files API).

The CID is always computed locally (bundle.cid_v1_raw). When Pinata is configured the CID it
returns must be identical, otherwise the attestation is refused: the on-chain cidDigest is
derived from the locally computed digest and must resolve to the pinned bytes.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import httpx

from .bundle import BundleError, cid_v1_raw, digest_from_cid, sha256

log = logging.getLogger("attestor.ipfs")

PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"


class IpfsError(RuntimeError):
    pass


class IpfsStore:
    def __init__(self, root: Path, pinata_jwt: str | None = None, gateway: str | None = None,
                 http: httpx.Client | None = None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._jwt = pinata_jwt
        self._gateway = gateway
        self._http = http or httpx.Client(timeout=30.0)

    @property
    def mode(self) -> str:
        return "local+pinata" if self._jwt else "local"

    def _path(self, cid: str) -> Path:
        digest_from_cid(cid)  # strict validation: the CID is used as a file name
        return self.root / cid

    def put(self, data: bytes) -> str:
        cid = cid_v1_raw(data)
        path = self._path(cid)
        if not path.exists():
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        if self._jwt:
            self._pin(cid, data)
        return cid

    def _pin(self, cid: str, data: bytes) -> None:
        try:
            resp = self._http.post(
                PINATA_UPLOAD_URL,
                headers={"Authorization": f"Bearer {self._jwt}"},
                data={"network": "public", "name": f"embedproof-{cid}.json"},
                files={"file": (f"{cid}.json", data, "application/json")},
            )
        except httpx.HTTPError as exc:
            raise IpfsError(f"Pinata upload failed: {exc}") from exc
        if resp.status_code >= 300:
            raise IpfsError(f"Pinata upload failed: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            returned = resp.json()["data"]["cid"]
        except Exception as exc:
            raise IpfsError(f"Pinata returned an unexpected response: {resp.text[:200]}") from exc
        if returned != cid:
            raise IpfsError(
                f"Pinata CID {returned} differs from the locally computed CIDv1/raw/sha2-256 {cid}; "
                "refusing to attest (the on-chain cidDigest must equal the digest inside the pinned CID)"
            )
        log.info("pinned %s on Pinata", cid)

    def get(self, cid: str) -> bytes | None:
        """Bundle bytes for `cid`, or None. Every read is re-hashed against the CID."""
        path = self._path(cid)
        data: bytes | None = path.read_bytes() if path.exists() else None
        if data is None and self._gateway:
            try:
                r = self._http.get(f"{self._gateway}/ipfs/{cid}")
                data = r.content if r.status_code == 200 else None
            except httpx.HTTPError:
                data = None
        if data is not None and sha256(data) != digest_from_cid(cid):
            raise BundleError(f"stored bytes for {cid} do not hash to the CID (storage corrupted)")
        return data
