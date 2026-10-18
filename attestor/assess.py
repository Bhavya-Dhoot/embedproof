"""Assessment additions (round 3): `hasHistory`, informational `flags` and the exact scorer `inputs`.

These sit next to the model's ScoreResult inside the attestation document's `assessment` block. They are computed by the
attestor from the same sources the scorer reads (the model artefact and the admin-maintained installations file), they are
informational only (a flag never changes the risk score), and `inputs` is sufficient for `python -m attestor.rescore` to
re-run the frozen model and reproduce the on-chain score.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

# Per-route electricity use (MWh per tonne of steel) assumed by Climate TRACE v5.10.0: iron-and-steel sources CSV, field
# `other3` ("electricity_use_factor", "MWh per t of steel"). It is Climate TRACE's own modelling assumption, uncited by
# Climate TRACE, and no sourced regulatory norm exists in this repository. The model does not use it as a feature
# (model/MODEL_CARD.md section 4). Modal values in data/raw/climate_trace/iron-and-steel_emissions_sources_v5_10_0.csv.gz:
# EAF 0.87 (32,124 of 32,472 rows), DRI-EAF 0.87 (3,812 of 3,828), BF/BOF 0.83 (20,296 of 20,988).
CT_ELECTRICITY_MWH_PER_T = {"EAF": 0.87, "BF-BOF": 0.83, "DRI-EAF": 0.87}
# Coarse sanity band chosen by EmbedProof (0.5x .. 2x of the assumption above). NOT a regulatory norm.
ELECTRICITY_BAND = (0.5, 2.0)
# A declared grid factor below this fraction of the Ember country-year value the model uses raises a flag.
GRID_FACTOR_FRACTION = 0.8

FLAG_GRID = "GRID_FACTOR_BELOW_EMBER"
FLAG_ELECTRICITY = "ELECTRICITY_OUTSIDE_ROUTE_NORM"

_DISPLAY_NAMES = {"TUR": "Türkiye"}  # Ember's spelling


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class Assessor:
    def __init__(self, artefact_path: Path, installations_path: Path, countries_path: Path | None = None):
        blob = Path(artefact_path).read_bytes()
        self.artefact_sha256 = "0x" + hashlib.sha256(blob).hexdigest()
        a = json.loads(blob.decode("utf-8"))
        self._grid = {iso3: {int(y): float(v) for y, v in series.items()} for iso3, series in a["grid_intensity_ember"].items()}
        self._iso2 = a["iso2_to_iso3"]
        td = a.get("training_data", {})
        self.data_version = {
            "source": td.get("source"),
            "snapshotSha256": td.get("snapshot_sha256"),
            "gridFixtureSha256": td.get("fixture_sha256", {}).get("grid_intensity_ember_annual.csv"),
        }
        path = Path(installations_path)
        self._installations = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self._names: dict[str, str] = {}
        cpath = Path(countries_path) if countries_path else REPO_ROOT / "data" / "fixtures" / "countries.csv"
        if cpath.exists():
            with open(cpath, encoding="utf-8", newline="") as fh:
                self._names = {row["iso3"]: row["name"] for row in csv.DictReader(fh)}

    # ------------------------------------------------------------------ helpers
    def _iso3(self, country: str | None) -> str | None:
        if not country:
            return None
        c = str(country).strip().upper()
        return c if len(c) == 3 else self._iso2.get(c)

    def _country_name(self, iso3: str | None) -> str:
        return _DISPLAY_NAMES.get(iso3) or self._names.get(iso3) or (iso3 or "the installation's country")

    def _grid_observation(self, iso3: str | None, year: int) -> tuple[float | None, int | None]:
        """Ember value and its data year, last observation carried forward (same rule as model.features.grid_lookup)."""
        series = self._grid.get(iso3) if iso3 else None
        years = [y for y in (series or {}) if y <= year]
        return (series[max(years)], max(years)) if years else (None, None)

    # ------------------------------------------------------------------ API
    def resolve(self, report: dict, installation: dict) -> dict:
        """The inputs the scorer ends up with for this (report, installation), mirroring Scorer.score_report."""
        entry = self._installations.get(installation.get("id") or report.get("installationId") or "")
        entry = entry if isinstance(entry, dict) else {}
        priors = entry.get("priors") or {}
        capacity = installation.get("capacityTpy") or priors.get("capacityTpy")
        country = installation.get("country") or entry.get("country")
        iso3 = self._iso3(country)
        year = int(str(report["period"])[:4])

        grid = _finite(priors.get("gridGco2PerKwh"))
        if grid is not None:
            grid_source = priors.get("gridSource") or "installations file (gridGco2PerKwh)"
            grid_year = next((y for y, v in sorted((self._grid.get(iso3) or {}).items(), reverse=True)
                              if abs(v - grid) < 1e-9), None)
        else:
            grid, grid_year = self._grid_observation(iso3, year)
            grid_source = "Ember yearly electricity data, power-sector CO2 intensity, last observation carried forward"
        prior = _finite(priors.get("ctLaggedPriorEf"))
        ref = entry.get("climateTraceReference") or {}
        return {
            "installation": {"id": installation.get("id"), "route": installation.get("route"),
                             "capacityTpy": capacity, "country": country},
            "features": {"route": installation.get("route"), "capacityTpy": capacity, "gridGco2PerKwh": grid,
                         "ctLaggedPriorEf": prior},
            "prior": {"value": prior, "source": priors.get("ctLaggedPriorSource") if prior is not None else None,
                      "sourceAssetId": ref.get("climateTraceSourceId") if prior is not None else None},
            "grid": {"value": grid, "source": grid_source, "observationYear": grid_year, "country": iso3},
            "dataVersion": self.data_version,
            "artefactSha256": self.artefact_sha256,
        }

    def flags(self, report: dict, inputs: dict) -> list[dict]:
        out: list[dict] = []
        grid = inputs["grid"]["value"]
        declared = _finite(report.get("gridFactorTco2PerMwh"))
        if grid is not None and declared is not None and declared < GRID_FACTOR_FRACTION * grid / 1000.0:
            year = inputs["grid"]["observationYear"]
            out.append({"code": FLAG_GRID, "message": (
                f"Declared grid factor {declared:.2f} tCO2/MWh is below Ember's {grid / 1000.0:.3f} for "
                f"{self._country_name(inputs['grid']['country'])}{f' {year}' if year else ''}; "
                "indirect emissions may be understated.")})
        route = inputs["features"]["route"]
        norm = CT_ELECTRICITY_MWH_PER_T.get(route)
        mwh, tonnes = _finite(report.get("electricityMwh")), _finite(report.get("productionTonnes"))
        if norm and mwh is not None and tonnes:
            per_t = mwh / tonnes
            lo, hi = ELECTRICITY_BAND[0] * norm, ELECTRICITY_BAND[1] * norm
            if per_t < lo or per_t > hi:
                out.append({"code": FLAG_ELECTRICITY, "message": (
                    f"Declared electricity use {per_t:.2f} MWh/t is outside {ELECTRICITY_BAND[0]}×–"
                    f"{ELECTRICITY_BAND[1]:g}× of Climate TRACE's modelling assumption ({norm} MWh/t for {route}); "
                    "coarse sanity band, not a regulatory norm.")})
        return out

    def enrich(self, report: dict, installation: dict, score: dict) -> dict:
        """ScoreResult + hasHistory / flags / inputs. Never alters riskScore, band or any model output."""
        inputs = self.resolve(report, installation)
        return {**score, "hasHistory": inputs["features"]["ctLaggedPriorEf"] is not None,
                "flags": self.flags(report, inputs), "inputs": inputs}
