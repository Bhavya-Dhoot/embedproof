"""EmbedProof scoring API (INTERFACES section 7).

    from model.scorer import Scorer
    s = Scorer.load()                       # loads model/artefacts/embedproof_model_v1.json
    s.model_version_hash                    # "0x" + sha256(artefact file bytes)
    s.score_report(report, installation)    # -> ScoreResult dict (INTERFACES section 6)

The scorer compares the DECLARED specific embedded emissions (SEE) with a plausible interval predicted from route,
nameplate capacity and national grid carbon intensity. It does not measure emissions; the training target is Climate
TRACE's own modelled estimate (see model/MODEL_CARD.md).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import shap

from model import features as F

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ARTEFACT = REPO / "model" / "artefacts" / "embedproof_model_v1.json"
DEFAULT_DEMO_INSTALLATIONS = REPO / "data" / "installations_demo.json"

GREEN_MAX, AMBER_MAX = 30, 60  # green <= 30, amber 31-60, red > 60
CBAM_HALF_PENALTY = 20
SHAP_PUBLIC_NAMES = {"route_code": "route", "capacity_tpy": "capacityTpy", "grid_gco2_kwh": "gridIntensity"}


def band_for(score: int) -> str:
    return "green" if score <= GREEN_MAX else ("amber" if score <= AMBER_MAX else "red")


def risk_score(q05: float, q50: float, see: float, below_half_default: bool) -> int:
    """r = 100 * clip((q05 - SEE) / (q50 - q05), 0, 1), +20 if SEE < 0.5 x CBAM default, clip [0, 100], round half up.

    Degenerate interval (q50 <= q05): 100 when SEE is below q05, else 0.
    """
    width = q50 - q05
    if width > 1e-12:
        frac = (q05 - see) / width
    else:
        frac = 1.0 if see < q05 else 0.0
    r = 100.0 * min(max(frac, 0.0), 1.0)
    if below_half_default:
        r += CBAM_HALF_PENALTY
    r = min(max(r, 0.0), 100.0)
    return int(math.floor(r + 0.5))


def _parse_period_year(period: str) -> int:
    try:
        y, m = str(period).split("-")
        year, month = int(y), int(m)
    except (ValueError, AttributeError):
        raise ValueError(f"period must be 'YYYY-MM', got {period!r}") from None
    if not (1 <= month <= 12 and 1900 <= year <= 2200):
        raise ValueError(f"period must be 'YYYY-MM', got {period!r}")
    return year


class Scorer:
    def __init__(self, artefact_path: Path, demo_path: Path | None = None):
        blob = Path(artefact_path).read_bytes()
        self._hash = "0x" + hashlib.sha256(blob).hexdigest()
        a = json.loads(blob.decode("utf-8"))
        if a.get("schema") != "embedproof.model.v1":
            raise ValueError(f"unexpected artefact schema {a.get('schema')!r}")
        self._a = a
        self._boosters = {k: lgb.Booster(model_str=a["boosters"][k]) for k in ("q05", "q50", "q95")}
        self._explainer = shap.TreeExplainer(self._boosters["q50"])
        self._offsets = a["post_processing"]["offsets"]
        self._ranges = a["training_ranges"]
        self._grid = {iso: {int(y): v for y, v in s.items()} for iso, s in a["grid_intensity_ember"].items()}
        self._iso2 = a["iso2_to_iso3"]
        self._cbam = a["cbam_defaults"]["by_country"]
        self._demo = {}
        dp = Path(demo_path) if demo_path else DEFAULT_DEMO_INSTALLATIONS
        if dp.exists():
            self._demo = json.loads(dp.read_text("utf-8"))

    @classmethod
    def load(cls, artefact_path: str | Path | None = None, demo_installations_path: str | Path | None = None) -> "Scorer":
        return cls(Path(artefact_path) if artefact_path else DEFAULT_ARTEFACT,
                   Path(demo_installations_path) if demo_installations_path else None)

    @property
    def model_version_hash(self) -> str:
        return self._hash

    # ------------------------------------------------------------------ helpers
    def _iso3(self, country: str | None) -> str | None:
        if not country:
            return None
        c = str(country).strip().upper()
        return c if len(c) == 3 else self._iso2.get(c)

    def cbam_default(self, iso3: str | None, route: str) -> float:
        by_route = self._cbam.get(iso3) if iso3 else None
        if not by_route or route not in by_route:
            by_route = self._cbam["OTHER"]
        return float(by_route[route]["total"])

    def _calibrated_interval(self, route: str, x: np.ndarray) -> tuple[float, float, float]:
        raw = np.sort([float(self._boosters[k].predict(x, num_threads=1)[0]) for k in ("q05", "q50", "q95")])
        o = self._offsets.get(route) or self._offsets["ALL"]
        q50 = float(raw[1])
        lo = max(min(float(raw[0]) - o["lower"], q50), 0.0)
        hi = max(float(raw[2]) + o["upper"], q50)
        return round(lo, 4), round(q50, 4), round(hi, 4)

    # ------------------------------------------------------------------ API
    def score_report(self, report: dict, installation: dict) -> dict:
        t0 = time.perf_counter()
        route = F.normalize_route(installation.get("route") or report.get("route"))
        if report.get("route") is not None and F.normalize_route(report["route"]) != route:
            raise ValueError(f"report route {report['route']!r} does not match installation route {installation.get('route')!r}")
        try:
            see = float(report["seeTco2PerT"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("report.seeTco2PerT is required and must be a number") from None
        if not math.isfinite(see) or see <= 0:
            raise ValueError(f"report.seeTco2PerT must be > 0, got {see}")
        year = _parse_period_year(report.get("period"))

        demo = self._demo.get(installation.get("id") or report.get("installationId") or "", {})
        priors = demo.get("priors", {})
        cap = installation.get("capacityTpy") or priors.get("capacityTpy")
        country = installation.get("country") or demo.get("country")
        iso3 = self._iso3(country)
        grid = priors.get("gridGco2PerKwh")
        if grid is None:
            grid = F.grid_lookup(self._grid, iso3, year)  # NaN when the country has no Ember row

        x = F.feature_row(route, cap, grid)
        q05, q50, q95 = self._calibrated_interval(route, x)

        default = round(self.cbam_default(iso3, route), 4)
        below_half = bool(see < 0.5 * default)
        score = risk_score(q05, q50, see, below_half)
        band = band_for(score)

        sv = np.asarray(self._explainer.shap_values(x, check_additivity=False))[0]
        order = np.argsort(-np.abs(sv), kind="stable")[:3]
        shap_out = []
        for i in order:
            name = F.FEATURES[i]
            v = x[0, i]
            value = route if name == "route_code" else (None if math.isnan(v) else float(v))
            shap_out.append({"feature": SHAP_PUBLIC_NAMES[name], "label": F.FEATURE_LABELS[name], "value": value,
                             "contribution": round(float(sv[i]), 4)})

        result = {
            "riskScore": score, "band": band,
            "q05": q05, "q50": q50, "q95": q95,
            "expectedLow": q05, "expectedHigh": q95,
            "seeDeclared": round(see, 6),
            "cbamDefault": default, "belowHalfDefault": below_half,
            "modelVersionHash": self._hash,
            "shap": shap_out,
            "explanation": self._explain(see, q05, q50, q95, route, cap, country, default, below_half, shap_out[0] if shap_out else None,
                                       self._ranges.get(route, {}).get("capacity_tpy")),
            "latencyMs": 0.0,
        }
        result["latencyMs"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return result

    @staticmethod
    def _explain(see, q05, q50, q95, route, cap, country, default, below_half, top, cap_range=None) -> str:
        article = "an" if route[0] in "AEIOU" else "a"
        where = f"{article} {route} plant" + (f" of {cap:,.0f} t/yr" if cap else "") + (f" in {country}" if country else "")
        if see < q05:
            pos = f"is {100 * (1 - see / q50):.0f}% below the model median {q50:.2f} and under the 5th-percentile bound {q05:.2f} of the plausible range {q05:.2f}-{q95:.2f}"
        elif see > q95:
            pos = f"is above the 95th-percentile bound {q95:.2f} of the plausible range {q05:.2f}-{q95:.2f} (median {q50:.2f})"
        else:
            pos = f"lies inside the plausible range {q05:.2f}-{q95:.2f} (median {q50:.2f})"
        text = f"Declared {see:.3f} tCO2/t {pos} for {where}"
        if below_half:
            text += f", and is under half of the CBAM default {default:.2f}"
        if top:
            text += f"; the largest driver of the expected value is {top['label'].lower()} ({top['contribution']:+.2f} tCO2/t)"
        if cap and cap_range and not (cap_range[0] <= cap <= cap_range[1]):
            text += f"; note: capacity is outside the {route} training range {cap_range[0]:,.0f}-{cap_range[1]:,.0f} t/yr, so its effect is extrapolated"
        return text + "."
