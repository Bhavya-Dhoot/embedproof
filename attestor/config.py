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
DEMO_RUNNER_CHAINS = (31337, 84532)  # hosted demo runner refuses to start anywhere else (mainnets, other testnets)

# Public Anvil test accounts (docs/INTERFACES.md section 1). Only ever used as defaults on chain 31337.
ANVIL_DEMO_KEYS = {
    "exporterA": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",  # account 2, operator TR-EAF-0001
    "exporterB": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",  # account 3, operator TR-BOF-0002
    "verifier": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",  # account 4
}
ANVIL_DEMO_IMPORTERS = {
    "importerA": "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",  # account 5
    "importerB": "0x976EA74026E726554dB657fA54763abd0C3a0aa9",  # account 6
}


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
class DemoRunnerConfig:
    """Hosted demo runner (DEMO_RUNNER=1). These are DEMO-ONLY keys: anyone who can reach the endpoints can make
    the service spend gas from them, so they must hold nothing of value (testnet dust)."""

    exporter_key: str  # operator of the demo installation(s)
    exporter_b_key: str | None  # operator of TR-BOF-0002 when it differs from exporter_key
    verifier_key: str  # holds VERIFIER_ROLE
    importer_a: str
    importer_b: str
    cooldown_seconds: float = 30.0


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
    installations_file: Path = REPO_ROOT / "data" / "installations_demo.json"  # admin-maintained plant -> prior mapping
    demo: DemoRunnerConfig | None = None  # set only when DEMO_RUNNER=1

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
    def from_env(cls, read_only: bool = False) -> "Config":
        """`read_only=True` (CLI tools that never sign) tolerates a missing ATTESTOR_PRIVATE_KEY on any chain."""
        load_env_files()
        env = os.environ

        chain_id = int(env.get("CHAIN_ID", str(LOCAL_CHAIN_ID)))
        key = (env.get("ATTESTOR_PRIVATE_KEY") or "").strip()
        if not key and read_only and chain_id != LOCAL_CHAIN_ID:
            from eth_account import Account

            key = "0x" + Account.create().key.hex()  # ephemeral, never used to sign
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
            installations_file=Path(env.get("INSTALLATIONS_FILE") or REPO_ROOT / "data" / "installations_demo.json"),
            demo=cls._demo_from_env(env, chain_id),
        )

    @staticmethod
    def _demo_from_env(env, chain_id: int) -> DemoRunnerConfig | None:
        if (env.get("DEMO_RUNNER") or "").strip() != "1":
            return None
        if chain_id not in DEMO_RUNNER_CHAINS:
            raise ConfigError(f"DEMO_RUNNER=1 is only allowed on chains {DEMO_RUNNER_CHAINS}, not {chain_id}")
        local = chain_id == LOCAL_CHAIN_ID

        def pick(name: str, default: str | None) -> str | None:
            value = (env.get(name) or "").strip()
            return value or (default if local else None)

        exporter = pick("DEMO_EXPORTER_KEY", ANVIL_DEMO_KEYS["exporterA"])
        verifier = pick("DEMO_VERIFIER_KEY", ANVIL_DEMO_KEYS["verifier"])
        imp_a = pick("DEMO_IMPORTER_A", ANVIL_DEMO_IMPORTERS["importerA"])
        imp_b = pick("DEMO_IMPORTER_B", ANVIL_DEMO_IMPORTERS["importerB"])
        missing = [n for n, v in (("DEMO_EXPORTER_KEY", exporter), ("DEMO_VERIFIER_KEY", verifier),
                                  ("DEMO_IMPORTER_A", imp_a), ("DEMO_IMPORTER_B", imp_b)) if not v]
        if missing:
            raise ConfigError(f"DEMO_RUNNER=1 on chain {chain_id} needs " + ", ".join(missing))
        from eth_utils import is_address

        for name, addr in (("DEMO_IMPORTER_A", imp_a), ("DEMO_IMPORTER_B", imp_b)):
            if not is_address(addr):
                raise ConfigError(f"{name} is not a valid address")
        return DemoRunnerConfig(
            exporter_key=_hex_key(exporter), exporter_b_key=_hex_key(pick("DEMO_EXPORTER_B_KEY", ANVIL_DEMO_KEYS["exporterB"])),
            verifier_key=_hex_key(verifier), importer_a=imp_a, importer_b=imp_b)


def _hex_key(value: str | None) -> str | None:
    if not value:
        return None
    return value if value.startswith("0x") else "0x" + value
