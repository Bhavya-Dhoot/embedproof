"""Building blocks shared by the headless demo (attestor/demo.py) and the hosted demo runner (attestor/demo_runner.py).

Both drive the same PRD scenarios; this module holds the parts that must not diverge: the public Anvil demo accounts,
how a report is signed, how an unused (installation, period) slot is chosen, and consignment ids.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Callable

from eth_account import Account

from .canonical import jcs, keccak256
from .config import ANVIL_DEMO_KEYS, REPO_ROOT
from .report import period_to_int
from .signing import sign_commitment

DEMO_REPORTS_DIR = REPO_ROOT / "data" / "demo_reports"
MAX_MONTHS_BACK = 300  # periods are searched backwards from the report's month down to ~2001

# Public Anvil test keys #2..#6 (docs/INTERFACES.md section 1). Never use them on a real network.
KEYS = {
    **ANVIL_DEMO_KEYS,
    "importerA": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    "importerB": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
}
ADDR = {k: Account.from_key(v).address for k, v in KEYS.items()}


def load_report(name: str, reports_dir: Path = DEMO_REPORTS_DIR) -> dict:
    return json.loads((reports_dir / name).read_text(encoding="utf-8"))


def sign_report(report: dict, key: str) -> str:
    """Exporter signature: EIP-191 personal_sign over the 32 raw bytes keccak256(JCS(report))."""
    return sign_commitment(keccak256(jcs(report)), key)[0]


def consignment_id(batch_id: int, tag: str, run_id: str) -> bytes:
    """bytes32 ASCII id, unique per run and batch (consignment ids are global on-chain)."""
    return f"EP-{batch_id}-{tag}-{run_id}".encode().ljust(32, b"\x00")


def run_id() -> str:
    return os.urandom(3).hex()


def months_back(period: str, count: int):
    """Yield 'YYYY-MM' for `period` and then up to `count` earlier months."""
    year, month = int(period[:4]), int(period[5:7])
    for _ in range(count + 1):
        yield f"{year:04d}-{month:02d}"
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)


def clamp_to_current_month(period: str, now_year: int, now_month: int) -> str:
    """The attestor refuses periods after the current UTC month; never start a search in the future."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{now_year:04d}-{now_month:02d}" if (year, month) > (now_year, now_month) else period


def free_period(batch_of: Callable[[str, int], int], status_of: Callable[[int], str], report: dict,
                max_back: int = MAX_MONTHS_BACK) -> dict:
    """Copy of `report` with the nearest period, going backwards from its own, whose (installation, period) slot is
    empty or Rejected (the only re-attestable state). Never later than the report's month: the attestor refuses
    future periods. Raises LookupError if the window is exhausted."""
    out = copy.deepcopy(report)
    for period in months_back(report["period"], max_back):
        existing = batch_of(report["installationId"], period_to_int(period))
        if existing == 0 or status_of(existing) == "Rejected":
            out["period"] = period
            return out
    raise LookupError(f"no free period for {report['installationId']} within {max_back} months of {report['period']}")
