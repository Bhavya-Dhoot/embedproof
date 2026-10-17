"""Shared fixtures: Anvil demo keys, a fake chain that mimics the contracts, a scorer double."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import pytest
from eth_account import Account
from eth_utils import keccak, to_checksum_address

from attestor.canonical import jcs, keccak256
from attestor.chain import STATUS, ChainRevert
from attestor.config import ANVIL_ATTESTOR_KEY, Config
from attestor.ipfs import IpfsStore
from attestor.report import installation_id_bytes32
from attestor.service import Attestor
from attestor.signing import MLDSASigner, recover_commitment_signer, sign_commitment
from attestor.store import Store

# Public Anvil test keys (docs/INTERFACES.md section 1). Never use on a real network.
KEYS = {
    "attestor": ANVIL_ATTESTOR_KEY,
    "exporterA": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "exporterB": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "verifier": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "importerA": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    "importerB": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
    "regulator": "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
}
ADDR = {k: Account.from_key(v).address for k, v in KEYS.items()}
ATTESTATION_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MODEL_HASH = "0x" + "ab" * 32

HONEST_REPORT = {
    "schema": "embedproof.report.v1", "installationId": "TR-EAF-0001", "period": "2026-09", "route": "EAF",
    "cnCode": "72083900", "productionTonnes": 5000, "directEmissionsTco2": 1150.0, "electricityMwh": 2250.0,
    "gridFactorTco2PerMwh": 0.42, "indirectEmissionsTco2": 945.0, "seeTco2PerT": 0.419,
}


def report(**over):
    r = copy.deepcopy(HONEST_REPORT)
    r.update(over)
    return r


def export_sign(rep: dict, key: str = KEYS["exporterA"]) -> str:
    return sign_commitment(keccak256(jcs(rep)), key)[0]


class FakeScorer:
    model_version_hash = MODEL_HASH

    def score_report(self, rep, installation):
        see = rep["seeTco2PerT"]
        risk = 88 if (installation["route"] == "BF-BOF" and see < 1.0) else 4  # BF-BOF mills cannot be that clean
        return {
            "riskScore": risk, "band": "green" if risk <= 30 else "red", "q05": 0.35, "q50": 0.5, "q95": 0.75,
            "expectedLow": 0.35, "expectedHigh": 0.75, "seeDeclared": see, "cbamDefault": 1.3,
            "belowHalfDefault": see < 0.65, "modelVersionHash": MODEL_HASH,
            "shap": [{"feature": "route", "label": "Production route", "value": "EAF", "contribution": -0.41}],
            "explanation": "test double", "latencyMs": 1.5,
        }


@dataclass
class _Tx:
    tx_hash: str
    gas_used: int


class FakeChain:
    """In-memory stand-in for chain.Chain that enforces the contract rules the attestor depends on."""

    chain_id = 31337
    attestor_address = ADDR["attestor"]
    attestation_address = ATTESTATION_ADDRESS

    def __init__(self):
        self.installations = {
            "TR-EAF-0001": {"id": "TR-EAF-0001", "country": "TR", "sector": "STEEL", "route": "EAF",
                            "capacityTpy": 100000, "operator": ADDR["exporterA"]},
            "TR-BOF-0002": {"id": "TR-BOF-0002", "country": "TR", "sector": "STEEL", "route": "BF-BOF",
                            "capacityTpy": 3500000, "operator": ADDR["exporterB"]},
        }
        self.batches: dict[int, dict] = {}
        self.by_period: dict[tuple[str, int], int] = {}
        self.roles = {("VERIFIER_ROLE", ADDR["verifier"]), ("REGULATOR_ROLE", ADDR["regulator"])}
        self.allocations: dict[int, set[str]] = {}
        self.approved = {bytes.fromhex(MODEL_HASH[2:])}
        self.pq_keys: dict[str, bytes] = {}  # registry.attestorPqKey; None return = registry predates the function
        self.pq_supported = True

    def connected(self):
        return True

    def installation(self, iid):
        return self.installations.get(iid)

    def operator_of(self, iid):
        return self.installations[iid]["operator"]

    def batch_of(self, iid, period):
        return self.by_period.get((iid, period), 0)

    def batch(self, batch_id):
        return self.batches.get(batch_id)

    def is_model_approved(self, h):
        return h in self.approved

    def has_role(self, role, address):
        return (role, to_checksum_address(address)) in self.roles

    def importers_of(self, batch_id):
        return self.allocations.get(batch_id, set())

    def attestor_pq_key(self, attestor):
        return self.pq_keys.get(to_checksum_address(attestor), bytes(32)) if self.pq_supported else None

    def find_batch_by_cid(self, cid):
        return next((b["batchId"] for b in self.batches.values() if b.get("_cid") == cid), None)

    def attest(self, a, cid):
        signer = recover_commitment_signer(a["commitment"], "0x" + a["ecdsaSig"].hex())  # contract: ecrecover
        if signer != self.attestor_address:
            raise ChainRevert("BadAttestorSignature", {"recovered": signer})
        iid = a["installationId"].rstrip(b"\x00").decode()
        key = (iid, a["period"])
        if key in self.by_period and self.batches[self.by_period[key]]["status"] != "Rejected":
            raise ChainRevert("DuplicatePeriod", {"installationId": "0x" + a["installationId"].hex(),
                                                   "period": a["period"], "existingBatchId": self.by_period[key]})
        batch_id = len(self.batches) + 1
        status = "PendingVerifier" if a["riskScore"] > 60 else "Attested"
        self.batches[batch_id] = {
            **{k: a[k] for k in ("installationId", "period", "seeDeclared", "verifiedTonnes", "riskScore", "algId",
                                 "commitment", "modelVersionHash", "pqSigHash", "cidDigest")},
            "status": status, "attestedAt": 1, "attestor": signer, "verifier": "0x" + "00" * 20,
            "reasonHash": b"\x00" * 32, "batchId": batch_id, "installationIdStr": iid, "_cid": cid,
        }
        self.by_period[key] = batch_id
        assert STATUS[1] == "PendingVerifier"
        return _Tx("0x" + keccak(text=f"tx{batch_id}").hex(), 187_000), {"batchId": batch_id, "status": status}


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(rpc_url="http://unused", chain_id=31337, attestor_private_key=KEYS["attestor"],
                  deployments_dir=tmp_path, pinata_jwt=None, ipfs_gateway=None, cors_origins=("http://localhost:3000",),
                  data_dir=tmp_path / "var", rate_burst=50, rate_per_sec=50, client_ip_header=None)


@pytest.fixture(scope="session")
def mldsa_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("mldsa-keys")
    MLDSASigner.load_or_create(d)  # one keygen per test session
    return d


@pytest.fixture
def chain() -> FakeChain:
    return FakeChain()


@pytest.fixture
def attestor(cfg, chain, mldsa_dir) -> Attestor:
    mldsa = MLDSASigner.load_or_create(mldsa_dir)
    chain.pq_keys[ADDR["attestor"]] = keccak256(mldsa.public_key)  # as deployed with ATTESTOR_PQ_KEY_HASH
    return Attestor(chain=chain, scorer=FakeScorer(), store=Store(cfg.db_path),
                    ipfs=IpfsStore(cfg.ipfs_dir), mldsa=mldsa,
                    attestor_key=cfg.attestor_private_key, clock=lambda: 1_790_000_000.0)
