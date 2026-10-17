"""ECDSA (EIP-191 over the commitment) and ML-DSA-44 (FIPS 204) signing.

ML-DSA backend order: oqs (liboqs-python) -> pqcrypto (PQClean C) -> dilithium_py
(pure-Python reference, labelled reference-only). All three implement pure ML-DSA-44
with an empty context, so keys and signatures are interchangeable between them.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from eth_account import Account
from eth_account.messages import encode_defunct

log = logging.getLogger("attestor.signing")

ML_DSA_44_PK_LEN = 1312
ML_DSA_44_SK_LEN = 2560
ML_DSA_44_SIG_LEN = 2420


class SignatureError(ValueError):
    pass


# --------------------------------------------------------------------------- ECDSA


def sign_commitment(commitment: bytes, private_key: str) -> tuple[str, str]:
    """EIP-191 personal_sign over the 32 raw commitment bytes. Returns (signature, signer)."""
    if len(commitment) != 32:
        raise SignatureError("commitment must be 32 bytes")
    signed = Account.sign_message(encode_defunct(primitive=commitment), private_key)
    return "0x" + signed.signature.hex(), Account.from_key(private_key).address


def recover_commitment_signer(commitment: bytes, signature: str) -> str:
    """Checksummed address that signed `commitment` under EIP-191. Raises SignatureError."""
    return _recover(encode_defunct(primitive=commitment), signature)


def recover_text_signer(message: str, signature: str) -> str:
    """Checksummed address that personal_signed the UTF-8 `message`."""
    return _recover(encode_defunct(text=message), signature)


def _recover(msg, signature: str) -> str:
    try:
        if not isinstance(signature, str) or not signature.startswith("0x") or len(signature) != 132:
            raise SignatureError("signature must be 65 bytes of 0x-hex")
        return Account.recover_message(msg, signature=signature)
    except SignatureError:
        raise
    except Exception as exc:
        raise SignatureError(f"unrecoverable signature: {exc}") from exc


# --------------------------------------------------------------------------- ML-DSA backends


@dataclass(frozen=True)
class _Backend:
    name: str  # module family
    label: str  # value for bundle.mldsa.backend
    keygen: Callable[[], tuple[bytes, bytes]]
    sign: Callable[[bytes, bytes], bytes]  # (sk, msg)
    verify: Callable[[bytes, bytes, bytes], bool]  # (pk, msg, sig)


def _load_oqs() -> _Backend:
    import oqs  # type: ignore

    if "ML-DSA-44" not in oqs.get_enabled_sig_mechanisms():
        # Older liboqs only has the pre-standard "Dilithium2", which is NOT FIPS 204.
        raise ImportError("liboqs build lacks final ML-DSA-44")

    def keygen():
        with oqs.Signature("ML-DSA-44") as s:
            pk = s.generate_keypair()
            return bytes(pk), bytes(s.export_secret_key())

    def sign(sk, msg):
        with oqs.Signature("ML-DSA-44", sk) as s:
            return bytes(s.sign(msg))

    def verify(pk, msg, sig):
        with oqs.Signature("ML-DSA-44") as s:
            return bool(s.verify(msg, sig, pk))

    return _Backend("oqs", "liboqs", keygen, sign, verify)


def _load_pqcrypto() -> _Backend:
    from pqcrypto.sign import ml_dsa_44 as m  # type: ignore

    def verify(pk, msg, sig):
        try:
            m.verify(pk, msg, sig)  # returns None on success, raises on failure
            return True
        except Exception:
            return False

    return _Backend("pqcrypto", "pqclean", lambda: tuple(m.keygen()), m.sign, verify)


def _load_dilithium_py() -> _Backend:
    from dilithium_py.ml_dsa import ML_DSA_44  # type: ignore

    def verify(pk, msg, sig):
        try:
            return bool(ML_DSA_44.verify(pk, msg, sig))
        except Exception:
            return False

    return _Backend(
        "dilithium_py", "dilithium-py-reference", ML_DSA_44.keygen, ML_DSA_44.sign, verify
    )


_LOADERS = {"oqs": _load_oqs, "pqcrypto": _load_pqcrypto, "dilithium_py": _load_dilithium_py}
DEFAULT_ORDER = ("oqs", "pqcrypto", "dilithium_py")


def select_backend(order: tuple[str, ...] | None = None) -> _Backend:
    """First importable backend in `order` (env PQ_BACKEND=name forces a single one)."""
    forced = os.environ.get("PQ_BACKEND")
    order = (forced,) if forced else (order or DEFAULT_ORDER)
    errors = []
    for name in order:
        try:
            backend = _LOADERS[name]()
            if backend.name == "dilithium_py":
                log.warning("ML-DSA backend is dilithium_py: pure-Python, reference-only, not constant-time")
            return backend
        except Exception as exc:  # ImportError, RuntimeError from liboqs build, ...
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    raise RuntimeError("no ML-DSA-44 backend available: " + "; ".join(errors))


class MLDSASigner:
    """ML-DSA-44 keypair with a persistent on-disk identity and a backend label."""

    def __init__(self, public_key: bytes, secret_key: bytes, backend: _Backend):
        self.public_key = public_key
        self._sk = secret_key
        self._backend = backend

    @property
    def backend(self) -> str:
        return self._backend.label

    def sign(self, message: bytes) -> bytes:
        sig = self._backend.sign(self._sk, message)
        if len(sig) != ML_DSA_44_SIG_LEN:
            raise SignatureError(f"unexpected ML-DSA-44 signature length {len(sig)}")
        return bytes(sig)

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        if len(public_key) != ML_DSA_44_PK_LEN or len(signature) != ML_DSA_44_SIG_LEN:
            return False
        return self._backend.verify(public_key, message, signature)

    @classmethod
    def load_or_create(cls, keys_dir: Path, order: tuple[str, ...] | None = None) -> "MLDSASigner":
        backend = select_backend(order)
        keys_dir.mkdir(parents=True, exist_ok=True)
        pk_path, sk_path = keys_dir / "mldsa44.pk", keys_dir / "mldsa44.sk"
        if pk_path.exists() and sk_path.exists():
            pk, sk = pk_path.read_bytes(), sk_path.read_bytes()
            if len(pk) != ML_DSA_44_PK_LEN or len(sk) != ML_DSA_44_SK_LEN:
                raise RuntimeError(f"corrupt ML-DSA key files in {keys_dir}; delete them to regenerate")
        else:
            pk, sk = backend.keygen()
            pk, sk = bytes(pk), bytes(sk)
            sk_path.write_bytes(sk)
            try:
                os.chmod(sk_path, 0o600)
            except OSError:
                pass
            pk_path.write_bytes(pk)
            (keys_dir / "mldsa44.meta.json").write_text(
                json.dumps({"alg": "ML-DSA-44", "generatedWith": backend.label}), encoding="utf-8"
            )
            log.info("generated ML-DSA-44 keypair in %s (backend %s)", keys_dir, backend.label)
        signer = cls(pk, sk, backend)
        probe = b"embedproof-key-selftest"  # fail loudly on a pk/sk mismatch or backend incompatibility
        if not signer.verify(pk, probe, signer.sign(probe)):
            raise RuntimeError("ML-DSA key self-test failed (public/secret key mismatch?)")
        return signer
