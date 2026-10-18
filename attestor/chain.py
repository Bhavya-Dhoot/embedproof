"""web3.py v8 contract access: reads, role checks, attest(), and custom-error decoding."""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from eth_abi import decode as abi_decode
from eth_account import Account
from eth_utils import keccak, to_checksum_address
from web3 import Web3
from web3.logs import DISCARD

from .config import Config
from .report import ROUTES, SECTORS, installation_id_bytes32

log = logging.getLogger("attestor.chain")

STATUS = ("None", "PendingVerifier", "Attested", "Rejected")
ROLES = ("OPERATOR_ROLE", "VERIFIER_ROLE", "ATTESTOR_ROLE", "REGULATOR_ROLE")
ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# Errors that can bubble up from OpenZeppelin base contracts even if a forge ABI omits them.
_FALLBACK_ERRORS = [
    ("EnforcedPause", []), ("ExpectedPause", []),
    ("AccessControlUnauthorizedAccount", [("address", "account"), ("bytes32", "neededRole")]),
    ("AccessControlBadConfirmation", []), ("InvalidInitialization", []), ("NotInitializing", []),
    ("UUPSUnauthorizedCallContext", []), ("UUPSUnsupportedProxiableUUID", [("bytes32", "slot")]),
    ("ERC1967InvalidImplementation", [("address", "implementation")]),
    ("Error", [("string", "message")]), ("Panic", [("uint256", "code")]),
]


class ChainError(RuntimeError):
    """RPC / deployment problem (not a contract revert)."""


class TxPending(ChainError):
    """Broadcast succeeded but no receipt arrived in time: the transaction may still land."""


class ChainRevert(ChainError):
    """A contract revert decoded to its custom-error name and arguments."""

    def __init__(self, name: str, args: dict[str, Any], raw: str | None = None):
        self.name, self.revert_args, self.raw = name, args, raw
        rendered = ", ".join(f"{k}={v}" for k, v in args.items())
        super().__init__(f"{name}({rendered})")


@dataclass
class TxResult:
    tx_hash: str
    gas_used: int
    block_number: int
    receipt: Any
    events: list[Any]


def _canonical_type(item: dict) -> str:
    t = item["type"]
    if t.startswith("tuple"):
        inner = ",".join(_canonical_type(c) for c in item["components"])
        return f"({inner}){t[len('tuple'):]}"
    return t


def _to_dict(components: list[dict], value: Any) -> dict[str, Any]:
    """Zip an ABI tuple's component names onto the decoded value (tuple/list/dict-like)."""
    if hasattr(value, "items"):
        return dict(value)
    return {c["name"]: v for c, v in zip(components, value)}


class Chain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        if not cfg.deployment_file.exists():
            raise ChainError(f"deployment file not found: {cfg.deployment_file} (deploy the contracts first)")
        dep = json.loads(cfg.deployment_file.read_text(encoding="utf-8"))
        if int(dep.get("chainId", cfg.chain_id)) != cfg.chain_id:
            raise ChainError(f"{cfg.deployment_file} is for chain {dep.get('chainId')}, expected {cfg.chain_id}")
        self.deployment = dep
        self.deploy_block = int(dep.get("deployBlock", 0))
        self.chain_id = cfg.chain_id

        self.w3 = Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 30}))
        abis = {n: self._load_abi(n) for n in ("InstallationRegistry", "EmissionsAttestation", "ConsignmentAllocator")}
        self.registry = self._contract(dep["registry"], abis["InstallationRegistry"])
        self.attestation = self._contract(dep["attestation"], abis["EmissionsAttestation"])
        self.allocator = self._contract(dep["allocator"], abis["ConsignmentAllocator"])
        self.accounts = dep.get("accounts", {})

        self._errors = self._build_error_table(abis.values())
        self._account = Account.from_key(cfg.attestor_private_key)
        self._tx_lock = threading.Lock()  # serialises nonce allocation + broadcast
        self._importers: dict[int, tuple[int, set[str]]] = {}  # batchId -> (last scanned block, importers)
        self._importers_lock = threading.Lock()

    # ------------------------------------------------------------------ setup helpers
    def _load_abi(self, name: str) -> list:
        path = self.cfg.abi_dir / f"{name}.json"
        if not path.exists():
            raise ChainError(f"ABI not found: {path}")
        abi = json.loads(path.read_text(encoding="utf-8"))
        return abi["abi"] if isinstance(abi, dict) else abi  # plain array per INTERFACES; tolerate forge artefact

    def _contract(self, address: str, abi: list):
        return self.w3.eth.contract(address=to_checksum_address(address), abi=abi)

    @staticmethod
    def _build_error_table(abis) -> dict[bytes, tuple[str, list[str], list[str]]]:
        table: dict[bytes, tuple[str, list[str], list[str]]] = {}
        for name, params in _FALLBACK_ERRORS:
            types = [t for t, _ in params]
            table[keccak(text=f"{name}({','.join(types)})")[:4]] = (name, types, [n for _, n in params])
        for abi in abis:
            for item in abi:
                if item.get("type") == "error":
                    types = [_canonical_type(i) for i in item.get("inputs", [])]
                    sig = f"{item['name']}({','.join(types)})"
                    table[keccak(text=sig)[:4]] = (
                        item["name"], types, [i.get("name") or f"arg{n}" for n, i in enumerate(item.get("inputs", []))],
                    )
        return table

    # ------------------------------------------------------------------ identity
    @property
    def attestor_address(self) -> str:
        return self._account.address

    @property
    def attestation_address(self) -> str:
        return self.attestation.address

    def connected(self) -> bool:
        try:
            return int(self.w3.eth.chain_id) == self.chain_id
        except Exception:
            return False

    # ------------------------------------------------------------------ revert decoding
    @staticmethod
    def _revert_hex(exc: BaseException) -> str | None:
        """Revert data, taken ONLY from `exc.data` (web3's ContractLogicError / ContractCustomError).

        Scraping hex out of str(exc) would let node-controlled error text pose as a contract revert."""
        data = getattr(exc, "data", None)
        if isinstance(data, dict):
            data = data.get("data")
        if isinstance(data, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2}){4,}", data):
            return data
        return None

    def decode_revert(self, data_hex: str) -> ChainRevert | None:
        raw = bytes.fromhex(data_hex[2:])
        entry = self._errors.get(raw[:4])
        if not entry:
            return None
        name, types, names = entry
        try:
            values = abi_decode(types, raw[4:])
        except Exception:
            return None
        args = {}
        for n, t, v in zip(names, types, values):
            if t == "address":
                v = to_checksum_address(v)
            elif isinstance(v, bytes):
                v = "0x" + v.hex()
            args[n] = v
        return ChainRevert(name, args, data_hex)

    def translate(self, exc: BaseException) -> ChainError:
        """Map a web3/RPC exception to a decoded ChainRevert or a generic ChainError.

        ChainError texts are safe to return to clients: RPC error strings can embed provider URLs with API
        keys, so the original exception is only logged."""
        if isinstance(exc, ChainError):
            return exc
        data = self._revert_hex(exc)
        if data:
            return self.decode_revert(data) or ChainRevert("UnknownRevert", {"data": data}, data)
        log.warning("chain call failed: %s: %s", type(exc).__name__, exc)
        return ChainError("chain RPC call failed")

    def _call(self, fn) -> Any:
        try:
            return fn.call()
        except Exception as exc:
            raise self.translate(exc) from exc

    # ------------------------------------------------------------------ reads
    def installation(self, installation_id: str) -> dict | None:
        id32 = installation_id_bytes32(installation_id)
        if self._call(self.registry.functions.operatorOf(id32)) == ZERO_ADDR:
            return None
        comps = self.registry.get_function_by_name("getInstallation").abi["outputs"][0]["components"]
        d = _to_dict(comps, self._call(self.registry.functions.getInstallation(id32)))
        return {
            "id": bytes(d["id"]).rstrip(b"\x00").decode("ascii"),
            "country": bytes(d["country"]).decode("ascii"),
            "sector": SECTORS[int(d["sector"])],
            "route": ROUTES[int(d["route"])],
            "capacityTpy": int(d["capacityTpy"]),
            "operator": to_checksum_address(d["operator"]),
        }

    def operator_of(self, installation_id: str) -> str:
        return to_checksum_address(self._call(self.registry.functions.operatorOf(installation_id_bytes32(installation_id))))

    def batch(self, batch_id: int) -> dict | None:
        """On-chain Batch as a dict (bytes fields kept as bytes); None if unknown."""
        if batch_id < 1 or batch_id > int(self._call(self.attestation.functions.batchCount())):
            return None
        comps = self.attestation.get_function_by_name("getBatch").abi["outputs"][0]["components"]
        d = _to_dict(comps, self._call(self.attestation.functions.getBatch(batch_id)))
        d["status"] = STATUS[int(d["status"])]
        d["installationIdStr"] = bytes(d["installationId"]).rstrip(b"\x00").decode("ascii")
        d["batchId"] = batch_id
        return d

    def batch_of(self, installation_id: str, period: int) -> int:
        return int(self._call(self.attestation.functions.batchOf(installation_id_bytes32(installation_id), period)))

    def is_model_approved(self, model_hash: bytes) -> bool:
        return bool(self._call(self.attestation.functions.isModelApproved(model_hash)))

    def has_role(self, role: str, address: str) -> bool:
        role_hash = keccak(text=role)
        return bool(self._call(self.registry.functions.hasRole(role_hash, to_checksum_address(address))))

    def attestor_pq_key(self, attestor: str) -> bytes | None:
        """registry.attestorPqKey(attestor): keccak256 of the attestor's ML-DSA-44 key (zero = unset).

        None if this registry deployment predates the function (redeploy with ATTESTOR_PQ_KEY_HASH)."""
        if not hasattr(self.registry.functions, "attestorPqKey"):
            log.warning("registry ABI has no attestorPqKey(address); the PQ key cannot be checked against the chain")
            return None
        return bytes(self._call(self.registry.functions.attestorPqKey(to_checksum_address(attestor))))

    def is_attestor_pq_key(self, attestor: str, pq_key_hash: bytes) -> bool | None:
        """registry.isAttestorPqKey(attestor, hash): true while that key hash is anchored (history, until revoked).

        None if this registry deployment predates the function."""
        if not hasattr(self.registry.functions, "isAttestorPqKey"):
            log.warning("registry ABI has no isAttestorPqKey(address,bytes32)")
            return None
        if pq_key_hash == bytes(32):
            return False
        return bool(self._call(self.registry.functions.isAttestorPqKey(to_checksum_address(attestor), pq_key_hash)))

    LOG_WINDOW = 5_000  # blocks per eth_getLogs request (public RPCs cap the range)

    def importers_of(self, batch_id: int) -> set[str]:
        """Importers with an Allocated event on `batch_id`. Cached incrementally per batch: only blocks after the
        last scanned one are queried, so the cache can never hide a new allocation."""
        try:
            latest = int(self.w3.eth.block_number)
            with self._importers_lock:
                last, found = self._importers.get(batch_id, (self.deploy_block - 1, set()))
                found = set(found)
                start = last + 1
                while start <= latest:
                    end = min(start + self.LOG_WINDOW - 1, latest)
                    for entry in self.allocator.events.Allocated().get_logs(
                            argument_filters={"batchId": batch_id}, from_block=start, to_block=end):
                        found.add(to_checksum_address(entry["args"]["importer"]))
                    start = end + 1
                self._importers[batch_id] = (max(last, latest), found)
                return set(found)
        except ChainError:
            raise
        except Exception as exc:
            raise self.translate(exc) from exc

    def find_batch_by_cid(self, cid: str) -> int | None:
        """Fallback when the local DB has no record: scan Attested logs (cid is not indexed)."""
        try:
            latest, start = int(self.w3.eth.block_number), self.deploy_block
            while start <= latest:
                end = min(start + self.LOG_WINDOW - 1, latest)
                for entry in self.attestation.events.Attested().get_logs(from_block=start, to_block=end):
                    if entry["args"]["cid"] == cid:
                        return int(entry["args"]["batchId"])
                start = end + 1
        except Exception as exc:
            raise self.translate(exc) from exc
        return None

    # ------------------------------------------------------------------ writes
    def send(self, fn, private_key: str) -> TxResult:
        """Estimate, sign locally, broadcast and wait. Reverts are decoded; status 0 raises."""
        acct = Account.from_key(private_key)
        with self._tx_lock:
            try:
                gas = fn.estimate_gas({"from": acct.address})
                tx = fn.build_transaction({
                    "from": acct.address,
                    "gas": int(gas * 1.25) + 5_000,
                    "nonce": self.w3.eth.get_transaction_count(acct.address, "pending"),
                    "chainId": self.chain_id,
                })
                tx_hash = self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
            except Exception as exc:
                raise self.translate(exc) from exc
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120, poll_latency=0.05)
        except Exception as exc:
            log.warning("no receipt for %s: %s", tx_hash.hex(), exc)
            raise TxPending(f"no receipt yet for 0x{tx_hash.hex().removeprefix('0x')}") from exc
        if receipt["status"] != 1:
            raise ChainRevert("TransactionReverted", {"txHash": "0x" + tx_hash.hex().removeprefix("0x")})
        return TxResult("0x" + tx_hash.hex().removeprefix("0x"), int(receipt["gasUsed"]),
                        int(receipt["blockNumber"]), receipt, [])

    def attest(self, a: dict[str, Any], cid: str) -> tuple[TxResult, dict[str, Any]]:
        """Call attest(AttestInput, cid) from the attestor key; returns (tx, Attested event args)."""
        comps = self.attestation.get_function_by_name("attest").abi["inputs"][0]["components"]
        struct = tuple(a[c["name"]] for c in comps)
        tx = self.send(self.attestation.functions.attest(struct, cid), self.cfg.attestor_private_key)
        events = self.attestation.events.Attested().process_receipt(tx.receipt, errors=DISCARD)
        mine = [e for e in events if e["address"] == self.attestation.address]
        if len(mine) != 1:
            raise ChainError(f"expected exactly one Attested event in {tx.tx_hash}, found {len(mine)}")
        args = dict(mine[0]["args"])
        args["status"] = STATUS[int(args["status"])]
        return tx, args

    # ------------------------------------------------------------------ verifier / allocator helpers (demo + runner)
    def verifier_sign(self, batch_id: int, approve: bool, reason_hash: bytes, key: str) -> TxResult:
        return self.send(self.attestation.functions.verifierSign(batch_id, approve, reason_hash), key)

    def allocate(self, batch_id: int, consignment_id: bytes, importer: str, cn_code: int, tonnes: int, key: str) -> TxResult:
        return self.send(self.allocator.functions.allocate(
            batch_id, consignment_id, to_checksum_address(importer), cn_code, tonnes), key)

    def claim(self, consignment_id: bytes, key: str) -> TxResult:
        return self.send(self.allocator.functions.claim(consignment_id), key)

    def allocate_expect_revert(self, batch_id: int, consignment_id: bytes, importer: str, cn_code: int, tonnes: int,
                               key: str, gas: int = 300_000) -> tuple[ChainRevert, str]:
        """Broadcast an allocate() that must revert, with a FIXED gas limit so it is mined with status 0 (a real,
        explorer-visible failed transaction) instead of being rejected by gas estimation.

        The revert reason is decoded from an eth_call simulation first. Returns (decoded revert, tx hash)."""
        acct = Account.from_key(key)
        fn = self.allocator.functions.allocate(batch_id, consignment_id, to_checksum_address(importer), cn_code, tonnes)
        try:
            fn.call({"from": acct.address})
        except Exception as exc:
            revert = self.translate(exc)
        else:
            raise ChainError("allocate() was expected to revert but the simulation succeeded")
        if not isinstance(revert, ChainRevert):
            raise revert
        with self._tx_lock:
            try:
                tx = fn.build_transaction({
                    "from": acct.address, "gas": gas, "chainId": self.chain_id,
                    "nonce": self.w3.eth.get_transaction_count(acct.address, "pending"),
                })
                tx_hash = self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
            except Exception as exc:
                raise self.translate(exc) from exc
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120, poll_latency=0.05)
        except Exception as exc:
            log.warning("no receipt for %s: %s", tx_hash.hex(), exc)
            raise TxPending(f"no receipt yet for 0x{tx_hash.hex().removeprefix('0x')}") from exc
        if receipt["status"] == 1:
            raise ChainError("allocate() was expected to revert but the transaction succeeded")
        return revert, "0x" + tx_hash.hex().removeprefix("0x")
