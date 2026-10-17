"""Build the training / lookup fixtures in data/fixtures/ from the raw snapshots in data/raw/.

Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe data/build_fixtures.py

Outputs (all deterministic CSV, '\n' line endings):
  fixtures/steel_facility_months.csv   one row per Climate TRACE steel plant-month (supported routes only)
  fixtures/steel_facilities.csv        plant metadata (id, name, country, route, lat, lon)
  fixtures/grid_intensity_ember_annual.csv   Ember annual power-sector CO2 intensity (gCO2/kWh)
  fixtures/cbam_defaults_steel.csv     EC CBAM default values (direct / indirect / total) for 3 representative CN codes
  fixtures/countries.csv               ISO2 <-> ISO3 <-> name (Climate TRACE definitions)

Nothing here is synthesised: every number is copied or arithmetically derived from a raw file.
"""
from __future__ import annotations

import calendar
import gzip
import json
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "fixtures"

# Climate TRACE `source_type` -> route string used in EmbedProof reports (INTERFACES section 4).
# Other CT types (IF, DRI-IF, BOF-only, mixed routes) are excluded: the contract has no matching route.
ROUTE_MAP = {"BF/BOF": "BF-BOF", "EAF": "EAF", "DRI-EAF": "DRI-EAF"}
EMBER_MAX_YEAR_CAP = 2025  # Ember yearly release has no 2026 row; 2026 plant-months reuse 2025 (documented)

# ASSUMPTION A-CBAM-1: EC defaults are published per CN code and exporting country, not per route.
# One representative CN code per route is used (see data/SOURCES.md).
CBAM_CN_FOR_ROUTE = {"BF-BOF": "7208", "EAF": "7214 20 00", "DRI-EAF": "7207 11 14"}
CBAM_OTHER_SHEET_PREFIX = "_Other Countries"

# CBAM sheet names that do not match the Climate TRACE country names (lower-cased, exact).
CBAM_SHEET_TO_ISO3 = {
    "korea, republic of (south korea": "KOR",
    "iran, islamic republic of": "IRN",
    "russian federation": "RUS",
    "viet nam": "VNM",
    "moldova, republic of": "MDA",
    "tanzania, united republic of": "TZA",
    "congo": "COG",
    "congo, democratic republic of": "COD",
    "ivory coast": "CIV",
    "laos": "LAO",
    "syria": "SYR",
    "brunei": "BRN",
    "hong kong": "HKG",
    "new caledonia and dependencies": "NCL",
    "north korea (democratic people’": "PRK",
    "bolivia": "BOL",
    "venezuela": "VEN",
    "taiwan": "TWN",
    "türkiye": "TUR",
    "curaçao": "CUW",
    "united kingdom": "GBR",
    "united states": "USA",
    "bosnia and herzegovina": "BIH",
    "trinidad and tobago": "TTO",
    "papua new guinea": "PNG",
    "north macedonia": "MKD",
}


def read_gz_csv(path: Path, **kw) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return pd.read_csv(fh, low_memory=False, **kw)


def build_plant_months() -> tuple[pd.DataFrame, pd.DataFrame]:
    src = read_gz_csv(RAW / "climate_trace" / "iron-and-steel_emissions_sources_v5_10_0.csv.gz")
    conf = read_gz_csv(RAW / "climate_trace" / "iron-and-steel_emissions_sources_confidence_v5_10_0.csv.gz")
    n_all = len(src)
    src = src[src["source_type"].isin(ROUTE_MAP)].copy()
    n_route = len(src)
    # Field semantics (detailed_data_schema_v5_10_0.csv, iron-and-steel columns): other1 = direct+indirect
    # emissions factor (t CO2e / t steel), other3 = electricity-use factor (MWh/t), other5 = grid intensity
    # (t/MWh), other6 = model methodology (text).
    assert (src["other1_def"] == "Direct and Indirect emissions factor: includes electricity use").all()
    assert (src["other6_def"] == "model_methodology").all()
    src["year"] = src["start_time"].str[:4].astype(int)
    src["month"] = src["start_time"].str[5:7].astype(int)
    dim = np.array([calendar.monthrange(y, m)[1] for y, m in zip(src["year"], src["month"])])
    diy = np.where([calendar.isleap(y) for y in src["year"]], 366, 365)
    # CT monthly capacity is the annual nameplate scaled by days-in-month / days-in-year; invert it.
    src["capacity_tpy"] = src["capacity"] * diy / dim
    src = src.merge(
        conf[["source_id", "start_time", "emissions_factor"]].rename(columns={"emissions_factor": "ct_ef_confidence"}),
        on=["source_id", "start_time"], how="left")
    n_before_cap = len(src)
    src = src[src["capacity_tpy"] > 0].copy()  # capacity 0 = plant closed / not yet operating in CT
    n_before_zero_ef = len(src)
    # EF == 0 appears only in plant-months with zero activity (satellite method sees no operation): the intensity
    # is undefined there, and a report with production > 0 can never legitimately declare 0.
    src = src[src["other1"] > 0].copy()
    out = pd.DataFrame({
        "source_id": src["source_id"].astype("int64"),
        "iso3": src["iso3_country"],
        "route": src["source_type"].map(ROUTE_MAP),
        "year": src["year"], "month": src["month"],
        "capacity_tpy": src["capacity_tpy"].round(1),
        "see_tco2_per_t": src["other1"].round(6),          # TARGET: Climate TRACE direct+indirect EF
        "ct_ef_direct": src["emissions_factor"].round(6),   # diagnostics only, never a feature
        "ct_capacity_factor": src["capacity_factor"].round(6),  # leakage ablation only, never a feature
        "ct_method": src["other6"],                         # diagnostics only, never a feature
        "ct_ef_confidence": src["ct_ef_confidence"],        # diagnostics only, never a feature
    })
    fac = (src.sort_values(["source_id", "start_time"]).groupby("source_id", as_index=False)
              .agg(name=("source_name", "first"), iso3=("iso3_country", "first"),
                   ct_type=("source_type", "first"), lat=("lat", "first"), lon=("lon", "first")))
    fac["route"] = fac["ct_type"].map(ROUTE_MAP)
    fac = fac[["source_id", "name", "iso3", "route", "ct_type", "lat", "lon"]]
    stats = {"rows_all_steel": n_all, "rows_supported_routes": n_route, "rows_capacity_zero_dropped": n_before_cap - n_before_zero_ef,
             "rows_zero_ef_dropped": n_before_zero_ef - len(src),
             "rows_fixture": len(out), "plants_fixture": int(out["source_id"].nunique())}
    return out.sort_values(["source_id", "year", "month"]).reset_index(drop=True), fac, stats


def build_grid() -> pd.DataFrame:
    e = pd.read_csv(RAW / "ember" / "ember_yearly_co2_intensity_gco2_per_kwh.csv")
    g = pd.DataFrame({"iso3": e["ISO 3 code"], "year": e["Year"], "gco2_per_kwh": e["Value"].round(2),
                      "area": e["Area"], "area_type": e["Area type"]})
    g = g[g["area_type"] == "Country or economy"].dropna(subset=["iso3", "gco2_per_kwh"])
    g = g[g["year"] >= 2018]
    return g[["iso3", "year", "gco2_per_kwh"]].sort_values(["iso3", "year"]).reset_index(drop=True)


def _num(cell):
    """EC workbook uses decimal commas and '-' / 'N/A' for not-applicable."""
    if cell is None:
        return None
    s = str(cell).strip()
    if s in ("", "-", "N/A", "see below"):
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def build_cbam(countries: pd.DataFrame) -> pd.DataFrame:
    wb = openpyxl.load_workbook(RAW / "cbam" / "cbam_default_values_definitive_period_v2_2026-08-06.xlsx",
                                read_only=True, data_only=True)
    name_to_iso3 = {n.lower(): a3 for n, a3 in zip(countries["name"], countries["iso3"])}
    name_to_iso3.update(CBAM_SHEET_TO_ISO3)
    rows, unmatched = [], []
    for sheet in wb.sheetnames:
        if sheet in ("Overview", "Version History", "Annex IV"):
            continue
        is_other = sheet.startswith(CBAM_OTHER_SHEET_PREFIX)
        iso3 = "OTHER" if is_other else name_to_iso3.get(sheet.lower())
        if iso3 is None:
            unmatched.append(sheet)
            continue
        found = {}
        for row in wb[sheet].iter_rows(values_only=True):
            cn = str(row[0]).strip() if row[0] is not None else ""
            if cn in CBAM_CN_FOR_ROUTE.values():
                found[cn] = (_num(row[2]), _num(row[3]), _num(row[4]), str(row[5] or ""))
        for route, cn in CBAM_CN_FOR_ROUTE.items():
            if cn in found and found[cn][2] is not None:
                d, i, t, leg = found[cn]
                rows.append({"iso3": iso3, "sheet": sheet, "route": route, "cn_code": cn,
                             "direct_tco2_per_t": d, "indirect_tco2_per_t": i, "total_tco2_per_t": t, "route_note": leg})
    assert not unmatched, f"unmatched CBAM sheets: {unmatched}"
    return pd.DataFrame(rows).sort_values(["iso3", "route"]).reset_index(drop=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    countries = pd.DataFrame(json.loads((RAW / "climate_trace" / "api_v6_definitions_countries.json").read_text("utf-8")))
    countries = countries.rename(columns={"alpha2": "iso2", "alpha3": "iso3"})[["iso2", "iso3", "name"]]
    countries.sort_values("iso3").to_csv(OUT / "countries.csv", index=False, lineterminator="\n")

    pm, fac, stats = build_plant_months()
    pm.to_csv(OUT / "steel_facility_months.csv", index=False, lineterminator="\n")
    fac.to_csv(OUT / "steel_facilities.csv", index=False, lineterminator="\n")

    grid = build_grid()
    grid.to_csv(OUT / "grid_intensity_ember_annual.csv", index=False, lineterminator="\n")

    cbam = build_cbam(countries)
    cbam.to_csv(OUT / "cbam_defaults_steel.csv", index=False, lineterminator="\n")

    stats.update({
        "countries_in_fixture": int(pm["iso3"].nunique()),
        "countries_without_ember_row": sorted(set(pm["iso3"]) - set(grid["iso3"])),
        "route_plants": pm.groupby("route")["source_id"].nunique().to_dict(),
        "year_range": [int(pm["year"].min()), int(pm["year"].max())],
        "cbam_country_sheets_with_steel_default": int(cbam[cbam["iso3"] != "OTHER"]["iso3"].nunique()),
    })
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
