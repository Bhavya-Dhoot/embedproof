"""Feature construction shared by training (model/train.py) and scoring (model/scorer.py).

Model v1 has exactly three inputs, all independent of the target:
  route_code       production route declared in the registry (BF-BOF=0, EAF=1, DRI-EAF=2)
  capacity_tpy     nameplate capacity (t/yr) of the installation
  grid_gco2_kwh    Ember annual power-sector CO2 intensity of the installation's country (gCO2/kWh)

Deliberately NOT features (see model/MODEL_CARD.md, "Leakage"): any Climate TRACE emissions, emissions factor,
activity, capacity factor, electricity-use factor, scrap share or methodology field, because the target is
Climate TRACE's own direct+indirect emissions factor and those fields are inputs to / outputs of that estimate.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "data" / "fixtures"

ROUTE_CODES = {"BF-BOF": 0, "EAF": 1, "DRI-EAF": 2}
FEATURES = ["route_code", "capacity_tpy", "grid_gco2_kwh"]
FEATURE_LABELS = {
    "route_code": "Production route",
    "capacity_tpy": "Nameplate capacity (t/yr)",
    "grid_gco2_kwh": "Grid carbon intensity (gCO2/kWh)",
}
TARGET = "see_tco2_per_t"


def normalize_route(route: str) -> str:
    """Map INTERFACES route spellings (report 'BF-BOF', enum 'BF_BOF', any case) to the model's route key.

    Raises ValueError for routes model v1 was not trained on (cement, unknown strings).
    """
    if not isinstance(route, str) or not route.strip():
        raise ValueError(f"route must be a non-empty string, got {route!r}")
    key = route.strip().upper().replace("_", "-")
    if key.startswith("CEMENT"):
        raise ValueError(f"Unsupported route {route!r}: model v1 is trained on steel only (BF-BOF, EAF, DRI-EAF); no cement model exists")
    if key not in ROUTE_CODES:
        raise ValueError(f"Unsupported route {route!r}: expected one of {sorted(ROUTE_CODES)}")
    return key


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_grid_table(path: Path | None = None) -> dict[str, dict[int, float]]:
    """{iso3: {year: gCO2/kWh}} from the Ember annual fixture."""
    g = pd.read_csv(path or FIXTURES / "grid_intensity_ember_annual.csv")
    table: dict[str, dict[int, float]] = {}
    for iso3, year, val in zip(g["iso3"], g["year"], g["gco2_per_kwh"]):
        table.setdefault(iso3, {})[int(year)] = float(val)
    return table


def grid_lookup(table: dict[str, dict[int, float]], iso3: str | None, year: int) -> float:
    """Latest Ember value with data year <= `year` (last-observation-carried-forward); NaN if the country has none.

    Ember's annual file ends at 2025 and is only partly populated for 2025, so 2026 rows and late-2025 rows
    reuse the most recent earlier year. Training and scoring use this one function.
    """
    series = table.get(iso3) if iso3 else None
    if not series:
        return math.nan
    years = [y for y in series if y <= year]
    return float(series[max(years)]) if years else math.nan


def load_training_frame() -> pd.DataFrame:
    """Plant-month fixture + model features. One row per Climate TRACE plant-month (supported routes, capacity > 0)."""
    df = pd.read_csv(FIXTURES / "steel_facility_months.csv")
    table = load_grid_table()
    pairs = df[["iso3", "year"]].drop_duplicates()
    lut = {(i, int(y)): grid_lookup(table, i, int(y)) for i, y in zip(pairs["iso3"], pairs["year"])}
    df["grid_gco2_kwh"] = [lut[(i, int(y))] for i, y in zip(df["iso3"], df["year"])]
    df["route_code"] = df["route"].map(ROUTE_CODES).astype(int)
    return df


def feature_row(route: str, capacity_tpy: float | None, grid_gco2_kwh: float | None) -> np.ndarray:
    cap = float(capacity_tpy) if capacity_tpy is not None and capacity_tpy > 0 else math.nan
    grid = float(grid_gco2_kwh) if grid_gco2_kwh is not None and not (isinstance(grid_gco2_kwh, float) and math.isnan(grid_gco2_kwh)) else math.nan
    return np.array([[ROUTE_CODES[normalize_route(route)], cap, grid]], dtype=np.float64)
