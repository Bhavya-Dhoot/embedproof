"""Environment-driven configuration (python-dotenv). Secrets are never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent

# Public Anvil test key #1 (0x70997970C51812dc3A010C7d01b50e0d17dc79C8). Local chain only.
ANVIL_ATTESTOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
LOCAL_CHAIN_ID = 31337


class ConfigError(RuntimeError):
    pass


def load_env_files() -> None:
    """Load attestor/.env and <repo>/.env; the real environment wins (no override)."""
    load_dotenv(PKG_DIR / ".env")
    load_dotenv(REPO_ROOT / ".env")


def data_dir_from_env() -> Path:
    """DATA_DIR (default attestor/var). Needs no chain or key settings, so key tooling can use it."""
    load_env_files()
    return Path(os.environ.get("DATA_DIR", str(PKG_DIR / "var")))


@dataclass(frozen=True)
class Config:
    rpc_url: str
    chain_id: int
    attestor_private_key: str
    deployments_dir: Path
    pinata_jwt: str | None
    ipfs_gateway: str | None
    cors_origins: tuple[str, ...]
    data_dir: Path
    rate_burst: float
    rate_per_sec: float
    client_ip_header: str | None  # e.g. "fly-client-ip" when behind a trusted proxy

    @property
    def db_path(self) -> Path:
        return self.data_dir / "attestor.db"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def ipfs_dir(self) -> Path:
        return self.data_dir / "ipfs"

    @property
    def deployment_file(self) -> Path:
        return self.deployments_dir / f"{self.chain_id}.json"

    @property
    def abi_dir(self) -> Path:
        return self.deployments_dir / "abi"

    @classmethod
    def from_env(cls) -> "Config":
        load_env_files()
        env = os.environ

        chain_id = int(env.get("CHAIN_ID", str(LOCAL_CHAIN_ID)))
        key = (env.get("ATTESTOR_PRIVATE_KEY") or "").strip()
        if not key:
            if chain_id != LOCAL_CHAIN_ID:
                raise ConfigError(
                    f"ATTESTOR_PRIVATE_KEY is required for chain {chain_id}; "
                    "the built-in Anvil key is only used for chain 31337"
                )
            key = ANVIL_ATTESTOR_KEY
        if not key.startswith("0x"):
            key = "0x" + key

        origins = env.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
        return cls(
            rpc_url=env.get("RPC_URL", "http://127.0.0.1:8545"),
            chain_id=chain_id,
            attestor_private_key=key,
            deployments_dir=Path(env.get("DEPLOYMENTS", str(REPO_ROOT / "deployments"))),
            pinata_jwt=(env.get("PINATA_JWT") or "").strip() or None,
            ipfs_gateway=((env.get("IPFS_GATEWAY") or "").strip().rstrip("/") or None),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            data_dir=Path(env.get("DATA_DIR", str(PKG_DIR / "var"))),
            rate_burst=float(env.get("RATE_BURST", "20")),
            rate_per_sec=float(env.get("RATE_PER_SEC", "2")),
            client_ip_header=(env.get("CLIENT_IP_HEADER") or "").strip().lower() or None,
        )
