"""AES-256-GCM encryption, bundle JSON (INTERFACES section 5) and CIDv1 computation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BUNDLE_SCHEMA = "embedproof.bundle.v1"
ALG_ID = 1
KEY_LEN, IV_LEN, TAG_LEN = 32, 12, 16

_CID_PREFIX = bytes([0x01, 0x55, 0x12, 0x20])  # CIDv1, raw codec, sha2-256, 32-byte digest


class BundleError(ValueError):
    pass


# ------------------------------------------------------------------ base64 helpers


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64_decode(text: str, what: str = "value") -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:
        raise BundleError(f"{what}: invalid base64") from exc


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str, what: str = "value") -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except Exception as exc:
        raise BundleError(f"{what}: invalid base64url") from exc


# ------------------------------------------------------------------ CIDv1 (raw, sha2-256)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def cid_from_digest(digest: bytes) -> str:
    if len(digest) != 32:
        raise BundleError("sha2-256 digest must be 32 bytes")
    return "b" + base64.b32encode(_CID_PREFIX + digest).decode("ascii").lower().rstrip("=")


def cid_v1_raw(data: bytes) -> str:
    """CIDv1, codec raw (0x55), multihash sha2-256, multibase base32 lower ('b' prefix)."""
    return cid_from_digest(sha256(data))


def digest_from_cid(cid: str) -> bytes:
    """Strictly parse a CIDv1/raw/sha2-256 string back to its digest (also guards file names)."""
    if not isinstance(cid, str) or len(cid) != 59 or cid[0] != "b" or cid != cid.lower():
        raise BundleError("not a base32 CIDv1 (raw, sha2-256)")
    body = cid[1:].upper()
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in body):
        raise BundleError("invalid base32 in CID")
    raw = base64.b32decode(body + "=" * (-len(body) % 8))
    if len(raw) != 36 or raw[:4] != _CID_PREFIX or cid_from_digest(raw[4:]) != cid:
        raise BundleError("CID is not canonical CIDv1 raw sha2-256")
    return raw[4:]


# ------------------------------------------------------------------ AES-256-GCM


def new_key() -> bytes:
    return os.urandom(KEY_LEN)


def encrypt(plaintext: bytes, key: bytes, aad: bytes, iv: bytes | None = None) -> tuple[bytes, bytes]:
    """AES-256-GCM. Returns (iv, ciphertext||tag). A fresh random 96-bit IV per call."""
    if len(key) != KEY_LEN:
        raise BundleError("AES key must be 32 bytes")
    iv = iv if iv is not None else os.urandom(IV_LEN)
    if len(iv) != IV_LEN:
        raise BundleError("IV must be 12 bytes")
    return iv, AESGCM(key).encrypt(iv, plaintext, aad)


def decrypt(ciphertext: bytes, key: bytes, iv: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_LEN or len(iv) != IV_LEN or len(ciphertext) < TAG_LEN:
        raise BundleError("malformed AES-GCM parameters")
    try:
        return AESGCM(key).decrypt(iv, ciphertext, aad)
    except InvalidTag as exc:
        raise BundleError("AES-GCM authentication failed (wrong key, AAD or tampered ciphertext)") from exc


# ------------------------------------------------------------------ bundle JSON


def build_bundle(
    *,
    chain_id: int,
    installation_id: str,
    period: int,
    commitment: bytes,
    ecdsa_signer: str,
    ecdsa_signature: str,
    mldsa_backend: str,
    mldsa_public_key: bytes,
    mldsa_signature: bytes,
    canonical: bytes,
    key: bytes,
    iv: bytes | None = None,
) -> bytes:
    """Serialise the encrypted bundle (compact UTF-8 JSON). AAD = the 32 raw commitment bytes."""
    iv, ct = encrypt(canonical, key, aad=commitment, iv=iv)
    obj: dict[str, Any] = {
        "schema": BUNDLE_SCHEMA,
        "algId": ALG_ID,
        "batchHint": {"chainId": chain_id, "installationId": installation_id, "period": period},
        "commitment": "0x" + commitment.hex(),
        "ecdsa": {"scheme": "secp256k1-eip191-keccak256", "signer": ecdsa_signer, "signature": ecdsa_signature},
        "mldsa": {
            "scheme": "ML-DSA-44",
            "backend": mldsa_backend,
            "publicKey": b64(mldsa_public_key),
            "signature": b64(mldsa_signature),
        },
        "encryption": {"alg": "AES-256-GCM", "iv": b64(iv), "aad": "commitment", "ciphertext": b64(ct)},
    }
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def parse_bundle(raw: bytes) -> dict[str, Any]:
    """Parse and structurally validate bundle bytes. Returns decoded binary fields."""
    try:
        b = json.loads(raw.decode("utf-8"))
        if b["schema"] != BUNDLE_SCHEMA or b["algId"] != ALG_ID:
            raise BundleError("unsupported bundle schema/algId")
        if b["encryption"]["alg"] != "AES-256-GCM" or b["encryption"]["aad"] != "commitment":
            raise BundleError("unsupported encryption parameters")
        if b["mldsa"]["scheme"] != "ML-DSA-44":
            raise BundleError("unsupported PQ scheme")
        commitment = bytes.fromhex(b["commitment"][2:]) if b["commitment"].startswith("0x") else b""
        if len(commitment) != 32:
            raise BundleError("bad commitment")
        return {
            "raw": b,
            "commitment": commitment,
            "ecdsaSigner": b["ecdsa"]["signer"],
            "ecdsaSignature": b["ecdsa"]["signature"],
            "mldsaPublicKey": b64_decode(b["mldsa"]["publicKey"], "mldsa.publicKey"),
            "mldsaSignature": b64_decode(b["mldsa"]["signature"], "mldsa.signature"),
            "iv": b64_decode(b["encryption"]["iv"], "encryption.iv"),
            "ciphertext": b64_decode(b["encryption"]["ciphertext"], "encryption.ciphertext"),
        }
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError(f"malformed bundle: {type(exc).__name__}: {exc}") from exc


def open_bundle(parsed: dict[str, Any], key: bytes) -> bytes:
    """Decrypt the canonical document bytes of a parsed bundle."""
    return decrypt(parsed["ciphertext"], key, parsed["iv"], aad=parsed["commitment"])
