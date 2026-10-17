"""Print the on-chain anchor of the attestor's ML-DSA-44 key:  python -m attestor.keys

Creates the key pair in <DATA_DIR>/keys if it does not exist yet (the same location and code path the
service uses), then prints ONLY `0x` + keccak256(raw public key) on stdout. Pass that value as
ATTESTOR_PQ_KEY_HASH when deploying so InstallationRegistry.setAttestorPqKey anchors it.
"""
from __future__ import annotations

import sys

from .canonical import hex0x, keccak256
from .config import data_dir_from_env
from .signing import MLDSASigner


def pq_key_hash(public_key: bytes) -> str:
    return hex0x(keccak256(public_key))


def main() -> int:
    signer = MLDSASigner.load_or_create(data_dir_from_env() / "keys")
    print(pq_key_hash(signer.public_key))
    print(f"ML-DSA-44 backend: {signer.backend}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
