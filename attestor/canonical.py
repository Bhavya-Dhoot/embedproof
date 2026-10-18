"""RFC 8785 (JCS) canonicalisation and keccak-256 commitments (INTERFACES section 5)."""
from __future__ import annotations

import math
import re
from typing import Any

import rfc8785
from eth_utils import keccak


class CanonicalizationError(ValueError):
    pass


def to_jsonable(obj: Any) -> Any:
    """Recursively reduce scorer/numpy output to plain JSON types.

    Rejects NaN/Infinity (not representable in JSON, so not in JCS either).
    """
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise CanonicalizationError("non-finite number cannot be canonicalised")
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    item = getattr(obj, "item", None)  # numpy scalar
    if callable(item):
        return to_jsonable(item())
    raise CanonicalizationError(f"unsupported type for canonical JSON: {type(obj).__name__}")


def jcs(obj: Any) -> bytes:
    """RFC 8785 canonical UTF-8 bytes.

    The rfc8785 library follows ES6 number serialisation and refuses integers
    outside +-(2^53-1), so the bytes are identical to JS `canonicalize`.
    """
    try:
        return rfc8785.dumps(to_jsonable(obj))
    except CanonicalizationError:
        raise
    except Exception as exc:  # FloatDomainError, IntegerDomainError, ...
        raise CanonicalizationError(str(exc)) from exc


def keccak256(data: bytes) -> bytes:
    return keccak(data)


def commitment_of(canonical: bytes) -> bytes:
    """commitment = keccak256(JCS(document))."""
    return keccak256(canonical)


def hex0x(data: bytes) -> str:
    return "0x" + data.hex()


_HEX_RE = re.compile(r"0x(?:[0-9a-fA-F]{2})*")


def from_hex(value: str, length: int | None = None) -> bytes:
    """Strict 0x + whole bytes of hex (bytes.fromhex would also swallow whitespace)."""
    if not isinstance(value, str) or not _HEX_RE.fullmatch(value):
        raise ValueError("expected 0x-prefixed hex string")
    raw = bytes.fromhex(value[2:])
    if length is not None and len(raw) != length:
        raise ValueError(f"expected {length} bytes, got {len(raw)}")
    return raw
