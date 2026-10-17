"""Report schema and validation (INTERFACES section 4) plus the on-chain field mappings."""
from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

REPORT_SCHEMA = "embedproof.report.v1"

# Route strings in the report <-> Route enum 0..4 in the registry.
ROUTES = ("BF-BOF", "EAF", "DRI-EAF", "CEMENT-DRY", "CEMENT-WET")
SECTORS = ("STEEL", "CEMENT")

REPORT_FIELDS = (
    "schema", "installationId", "period", "route", "cnCode", "productionTonnes",
    "directEmissionsTco2", "electricityMwh", "gridFactorTco2PerMwh", "indirectEmissionsTco2", "seeTco2PerT",
)
_NUMERIC = (
    "productionTonnes", "directEmissionsTco2", "electricityMwh", "gridFactorTco2PerMwh",
    "indirectEmissionsTco2", "seeTco2PerT",
)
TOLERANCE = 0.01  # 1 %
MAX_SAFE = 2**53 - 1  # JCS / JSON-number interoperability bound

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")  # all three used with fullmatch: `$` accepts a trailing newline
_PERIOD_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")
_CN_RE = re.compile(r"[0-9]{4,8}")


class ReportError(ValueError):
    """Validation failure; `errors` lists every problem found (reported as HTTP 422)."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _close(actual: float, expected: float) -> bool:
    if expected == 0:
        return abs(actual) <= 1e-9
    return abs(actual - expected) <= TOLERANCE * abs(expected)


def validate_report(report: Any, installation: dict | None) -> None:
    """Validate a report against section 4 and the registry entry `installation`.

    `installation` is the chain.Installation dict ({"route": "EAF", ...}) or None if unknown.
    Raises ReportError listing every failure.
    """
    if not isinstance(report, dict):
        raise ReportError(["report must be a JSON object"])
    errors: list[str] = []

    if report.get("schema") != REPORT_SCHEMA:
        errors.append(f"schema must be {REPORT_SCHEMA!r}")
    missing = [f for f in REPORT_FIELDS if f not in report]
    extra = sorted(set(report) - set(REPORT_FIELDS))
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if extra:
        # Unknown fields would be signed and committed verbatim; refuse rather than carry them.
        errors.append("unknown fields: " + ", ".join(extra))

    iid = report.get("installationId")
    if not (isinstance(iid, str) and _ID_RE.fullmatch(iid)):
        errors.append("installationId must be 1-32 chars of [A-Za-z0-9_-]")
    period = report.get("period")
    m = _PERIOD_RE.fullmatch(period) if isinstance(period, str) else None
    if not m or not (2000 <= int(m.group(1)) <= 2100):
        errors.append("period must be YYYY-MM (year 2000-2100)")
    route = report.get("route")
    if route not in ROUTES:
        errors.append("route must be one of " + " | ".join(ROUTES))
    cn = report.get("cnCode")
    if not (isinstance(cn, str) and _CN_RE.fullmatch(cn)):
        errors.append("cnCode must be a string of 4-8 digits")

    bad_numeric = [f for f in _NUMERIC if f in report and not _is_num(report[f])]
    if bad_numeric:
        errors.append("must be finite numbers: " + ", ".join(bad_numeric))
    if errors:  # numeric rules below assume well-typed fields
        raise ReportError(errors)

    nums = {f: float(report[f]) for f in _NUMERIC}
    for f, v in nums.items():
        if v < 0 or v > MAX_SAFE:
            errors.append(f"{f} out of range")
    if not nums["productionTonnes"] >= 1:
        errors.append("productionTonnes must be >= 1 (whole tonnes of product are attested)")
    if nums["seeTco2PerT"] * 1e4 >= 2**32:
        errors.append("seeTco2PerT too large for on-chain representation")
    if not errors:
        expected_indirect = nums["electricityMwh"] * nums["gridFactorTco2PerMwh"]
        if not _close(nums["indirectEmissionsTco2"], expected_indirect):
            errors.append(
                f"indirectEmissionsTco2 {nums['indirectEmissionsTco2']} is not within 1% of "
                f"electricityMwh x gridFactorTco2PerMwh = {expected_indirect:.6g}"
            )
        expected_see = (nums["directEmissionsTco2"] + nums["indirectEmissionsTco2"]) / nums["productionTonnes"]
        if not _close(nums["seeTco2PerT"], expected_see):
            errors.append(
                f"seeTco2PerT {nums['seeTco2PerT']} is not within 1% of "
                f"(direct + indirect) / production = {expected_see:.6g}"
            )

    if installation is None:
        errors.append(f"installation {iid!r} is not registered on-chain")
    elif installation["route"] != route:
        errors.append(
            f"route {route!r} does not match the registry route {installation['route']!r} for {iid}"
        )
    if errors:
        raise ReportError(errors)


# ---------------------------------------------------------------- on-chain field mappings


def installation_id_bytes32(installation_id: str) -> bytes:
    """ASCII, right-padded with zero bytes (INTERFACES section 2)."""
    raw = installation_id.encode("ascii")
    if not 1 <= len(raw) <= 32:
        raise ValueError("installation id must be 1-32 ASCII bytes")
    return raw.ljust(32, b"\x00")


def period_to_int(period: str) -> int:
    """'2026-09' -> 202609."""
    return int(period[:4]) * 100 + int(period[5:7])


def see_to_chain(see: float) -> int:
    """tCO2/t x 1e4, round half up. Decimal(repr) avoids binary-float artefacts (0.4195 -> 4195)."""
    return int((Decimal(repr(float(see))) * 10000).to_integral_value(rounding=ROUND_HALF_UP))


def tonnes_to_chain(production: float) -> int:
    """Whole tonnes, rounded down: never verify more product than was reported."""
    return int(math.floor(float(production)))
