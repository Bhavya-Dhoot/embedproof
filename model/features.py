"""Feature construction shared by training (model/train.py) and scoring (model/scorer.py).

Model inputs (4):
  route_code       production route declared in the registry (BF-BOF=0, EAF=1, DRI-EAF=2)
  capacity_tpy     nameplate capacity (t/yr) of the installation
  grid_gco2_kwh    Ember annual power-sector CO2 intensity of the installation's country (gCO2/kWh)
  ct_prior_ef      LAGGED Climate TRACE prior: the plant's OWN Climate TRACE direct emissions factor (tCO2e per t steel),
                   averaged over the months m-14 .. m-3 before the report month m (>= 6 months present, else NaN).

The prior is a persistence signal. With Climate TRACE also supplying the ground truth, metrics measure how consistent a
claim is with Climate TRACE's own history, not real-world accuracy (see model/MODEL_CARD.md).

Still deliberately NOT features: same-or-recent-month Climate TRACE emissions / activity / capacity factor / electricity
factor / scrap share / methodology, and the Climate TRACE direct+indirect factor itself (it is the target).
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
FEATURES = ["route_code", "capacity_tpy", "grid_gco2_kwh", "ct_prior_ef"]
FEATURE_LABELS = {
    "route_code": "Production route",
    "capacity_tpy": "Nameplate capacity (t/yr)",
    "grid_gco2_kwh": "Grid carbon intensity (gCO2/kWh)",
    "ct_prior_ef": "Climate TRACE lagged emissions-factor prior (tCO2e/t)",
}
TARGET = "see_tco2_per_t"

# Lagged-prior definition. Climate TRACE publishes with roughly a 2-month delay (PRD section 8), and a report for month m
# is filed after m ends, so the freshest CT month a verifier can rely on is about m-2. The window therefore ENDS at m-3
# (one month of safety margin) and spans 12 months (m-14 .. m-3) to average out seasonality and CT's monthly grid noise.
PRIOR_LAG_END = 3
PRIOR_LAG_START = 14
PRIOR_MIN_MONTHS = 6


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


def lagged_mean(df: pd.DataFrame, col: str, lag_end: int = PRIOR_LAG_END, lag_start: int = PRIOR_LAG_START,
                min_months: int = PRIOR_MIN_MONTHS) -> tuple[np.ndarray, np.ndarray]:
    """Per plant-month: mean of `col` over the same plant's months m-lag_start .. m-lag_end (inclusive).

    Uses ONLY earlier months of the same plant (never month m, m-1 or m-2). NaN when fewer than `min_months` of the
    12 window months exist in the data (new plant, early 2021, or long closures). Returns (mean, months_available).
    """
    t_all = ((df["year"] - 2021) * 12 + df["month"] - 1).to_numpy()
    vals = df[col].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    cnt = np.zeros(len(df), dtype=int)
    for _, idx in df.groupby("source_id").indices.items():
        t = t_all[idx]
        t0 = t.min() - lag_start
        n = t.max() - t0 + 1
        arr = np.zeros(n)
        has = np.zeros(n)
        arr[t - t0] = vals[idx]
        has[t - t0] = 1.0
        cs = np.concatenate([[0.0], np.cumsum(arr)])
        ch = np.concatenate([[0.0], np.cumsum(has)])
        hi = t - lag_end - t0 + 1
        lo = t - lag_start - t0
        s = cs[hi] - cs[lo]
        c = (ch[hi] - ch[lo]).astype(int)
        out[idx] = np.where(c >= min_months, s / np.maximum(c, 1), np.nan)
        cnt[idx] = c
    return out, cnt


def load_training_frame() -> pd.DataFrame:
    """Plant-month fixture + model features. One row per Climate TRACE plant-month (supported routes, capacity > 0)."""
    df = pd.read_csv(FIXTURES / "steel_facility_months.csv").sort_values(["source_id", "year", "month"]).reset_index(drop=True)
    table = load_grid_table()
    pairs = df[["iso3", "year"]].drop_duplicates()
    lut = {(i, int(y)): grid_lookup(table, i, int(y)) for i, y in zip(pairs["iso3"], pairs["year"])}
    df["grid_gco2_kwh"] = [lut[(i, int(y))] for i, y in zip(df["iso3"], df["year"])]
    df["route_code"] = df["route"].map(ROUTE_CODES).astype(int)
    df["ct_prior_ef"], df["prior_months"] = lagged_mean(df, "ct_ef_direct")
    # reference only (never a feature): persistence of the plant's own lagged direct+indirect SEE
    df["hist_see_mean"], _ = lagged_mean(df, TARGET)
    return df


def _num_or_nan(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def feature_row(route: str, capacity_tpy: float | None, grid_gco2_kwh: float | None, ct_prior_ef: float | None = None) -> np.ndarray:
    cap = _num_or_nan(capacity_tpy)
    cap = cap if cap > 0 else math.nan
    prior = _num_or_nan(ct_prior_ef)
    prior = prior if prior > 0 else math.nan
    return np.array([[ROUTE_CODES[normalize_route(route)], cap, _num_or_nan(grid_gco2_kwh), prior]], dtype=np.float64)
