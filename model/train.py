"""Train, freeze and evaluate the EmbedProof plausibility model (LightGBM quantile regression).

Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe -m model.train

Pipeline
  1. Load data/fixtures/steel_facility_months.csv (Climate TRACE v5.10.0 plant-months) + Ember grid intensity.
  2. Split PLANTS (never rows) 64 / 16 / 20 % into train / calibration / test, stratified by route.
  3. Choose hyper-parameters by 5-fold grouped CV on the train plants only. The test plants are not touched
     until step 6.
  4. Fit q = 0.05 / 0.50 / 0.95 boosters on the train plants; sort the three predictions per row (monotone
     rearrangement, removes quantile crossing); widen/narrow the interval with one-sided split-conformal offsets
     computed on the calibration plants.
  5. Freeze everything into ONE json file (model/artefacts/embedproof_model_v1.json);
     modelVersionHash = "0x" + sha256(file bytes). Training is run twice and must give identical bytes.
  6. Evaluate once on the held-out test plants -> model/artefacts/eval.json (+ SHAP stability, latency).

Everything is deterministic (deterministic=True, force_row_wise, 1 thread, fixed seeds).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.model_selection import GroupKFold

from model import features as F

ARTEFACT_DIR = Path(__file__).resolve().parent / "artefacts"
ARTEFACT_PATH = ARTEFACT_DIR / "embedproof_model_v1.json"
HASH_PATH = ARTEFACT_DIR / "model_version.txt"
EVAL_PATH = ARTEFACT_DIR / "eval.json"

QUANTILES = (0.05, 0.50, 0.95)
SPLIT_SEED = 20261018
FINAL_SEED = 7
SHAP_SEEDS = (101, 102, 103, 104, 105)
BASE_PARAMS = dict(objective="quantile", learning_rate=0.05, bagging_fraction=0.8, bagging_freq=1, max_bin=32,
                   verbose=-1, num_threads=1, deterministic=True, force_row_wise=True)
HP_GRID = [dict(num_leaves=nl, min_data_in_leaf=md) for nl in (4, 7) for md in (200, 400, 1000)]
ROUNDS = (200, 500)
DETECTION_FACTORS = (0.5, 0.7, 0.85)
RED, AMBER = 60, 30
CBAM_HALF = 0.5
BOOTSTRAPS = 2000


# ----------------------------------------------------------------------------- data / splits
def make_split(df: pd.DataFrame) -> pd.DataFrame:
    """Plant-level split, stratified by route. 20 % test, 16 % calibration, rest train."""
    rng = np.random.RandomState(SPLIT_SEED)
    plants = df.groupby("source_id")["route"].first().reset_index().sort_values("source_id")
    split: dict[int, str] = {}
    for _, g in plants.groupby("route", sort=True):
        ids = g["source_id"].to_numpy().copy()
        rng.shuffle(ids)
        n = len(ids)
        n_test, n_cal = int(round(0.20 * n)), int(round(0.16 * n))
        for i in ids[:n_test]:
            split[int(i)] = "test"
        for i in ids[n_test:n_test + n_cal]:
            split[int(i)] = "calib"
        for i in ids[n_test + n_cal:]:
            split[int(i)] = "train"
    df = df.copy()
    df["split"] = df["source_id"].map(split)
    return df


# ----------------------------------------------------------------------------- model primitives
def fit_booster(X: pd.DataFrame, y: np.ndarray, alpha: float, hp: dict, rounds: int, seed: int) -> lgb.Booster:
    params = dict(BASE_PARAMS, alpha=alpha, seed=seed, **hp)
    return lgb.train(params, lgb.Dataset(X, y), num_boost_round=rounds)


def fit_triplet(X: pd.DataFrame, y: np.ndarray, hp: dict, rounds: int, seed: int) -> list[lgb.Booster]:
    return [fit_booster(X, y, a, hp, rounds, seed) for a in QUANTILES]


def predict_sorted(boosters: list[lgb.Booster], X, num_iteration: int | None = None) -> np.ndarray:
    P = np.column_stack([b.predict(X, num_iteration=num_iteration) for b in boosters])
    return np.sort(P, axis=1)  # monotone rearrangement: q05 <= q50 <= q95 by construction


def pinball(y: np.ndarray, q: np.ndarray, a: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(a * d, (a - 1) * d)))


def mean_pinball(y: np.ndarray, P: np.ndarray) -> float:
    return float(np.mean([pinball(y, P[:, j], a) for j, a in enumerate(QUANTILES)]))


def grouped_cv(df: pd.DataFrame, feats: list[str], hp: dict, rounds_list=ROUNDS, seed: int = FINAL_SEED,
               group_col: str = "source_id") -> dict:
    """5-fold grouped CV on the TRAIN plants (groups = plants by default, countries for the leave-country-out check).

    Returns metrics per number of rounds, including the route-median baseline fitted inside each training fold.
    """
    X = df[feats].copy()
    if "iso3" in feats:
        X["iso3"] = X["iso3"].astype("category")
    y = df[F.TARGET].to_numpy()
    out = {r: np.zeros((len(df), 3)) for r in rounds_list}
    base = np.zeros(len(df))
    for tr, va in GroupKFold(n_splits=5).split(X, y, df[group_col]):
        med = df.iloc[tr].groupby("route")[F.TARGET].median()
        base[va] = df.iloc[va]["route"].map(med).to_numpy()
        for j, a in enumerate(QUANTILES):
            b = fit_booster(X.iloc[tr], y[tr], a, hp, max(rounds_list), seed)
            for r in rounds_list:
                out[r][va, j] = b.predict(X.iloc[va], num_iteration=r)
    res = {}
    for r in rounds_list:
        P = np.sort(out[r], axis=1)
        res[r] = {"pinball": mean_pinball(y, P), "mae_q50": float(np.mean(np.abs(y - P[:, 1]))),
                  "mae_route_median_baseline": float(np.mean(np.abs(y - base))),
                  "coverage_raw": float(np.mean((y >= P[:, 0]) & (y <= P[:, 2])))}
    return res


# ----------------------------------------------------------------------------- calibration + risk
def conformal_offsets(y: np.ndarray, P: np.ndarray, routes: np.ndarray) -> dict:
    """One-sided split-conformal offsets so each tail holds ~5 % of calibration rows.

    lower_offset = 95th percentile of (q05 - y)  -> q05' = q05 - lower_offset
    upper_offset = 95th percentile of (y - q95)  -> q95' = q95 + upper_offset
    Per route when a route has >= 30 calibration plants (computed by the caller), else pooled ("ALL").
    """
    def offs(mask):
        return (float(np.quantile(P[mask, 0] - y[mask], 0.95, method="higher")),
                float(np.quantile(y[mask] - P[mask, 2], 0.95, method="higher")))

    allmask = np.ones(len(y), dtype=bool)
    lo, hi = offs(allmask)
    return {"ALL": {"lower": lo, "upper": hi, "n_rows": int(len(y))}} | {
        r: dict(zip(("lower", "upper"), offs(routes == r))) | {"n_rows": int((routes == r).sum())}
        for r in np.unique(routes)}


def apply_calibration(P: np.ndarray, routes: np.ndarray, offsets: dict) -> np.ndarray:
    lo = np.empty(len(P))
    hi = np.empty(len(P))
    for i, r in enumerate(routes):
        o = offsets.get(r) or offsets["ALL"]
        lo[i] = P[i, 0] - o["lower"]
        hi[i] = P[i, 2] + o["upper"]
    q50 = P[:, 1]
    lo = np.clip(np.minimum(lo, q50), 0.0, None)  # SEE >= 0; interval always contains the median
    hi = np.maximum(hi, q50)
    return np.column_stack([lo, q50, hi])


def risk_vec(q05: np.ndarray, q50: np.ndarray, see: np.ndarray, below_half: np.ndarray) -> np.ndarray:
    """Vectorised twin of model.scorer.risk_score (INTERFACES section 7). Inputs already rounded to 4 dp."""
    width = q50 - q05
    safe = np.where(width > 1e-12, width, 1.0)
    frac = np.where(width > 1e-12, (q05 - see) / safe, np.where(see < q05, 1.0, 0.0))
    r = np.clip(100.0 * np.clip(frac, 0.0, 1.0) + 20.0 * below_half, 0.0, 100.0)
    return np.floor(r + 0.5).astype(int)


# ----------------------------------------------------------------------------- artefact
def cbam_tables() -> tuple[dict, pd.DataFrame]:
    c = pd.read_csv(F.FIXTURES / "cbam_defaults_steel.csv")
    by_country: dict = {}
    for r in c.itertuples():
        by_country.setdefault(r.iso3, {})[r.route] = {
            "cn_code": r.cn_code, "direct": r.direct_tco2_per_t,
            "indirect": None if pd.isna(r.indirect_tco2_per_t) else r.indirect_tco2_per_t,
            "total": r.total_tco2_per_t, "sheet": r.sheet, "route_note": None if pd.isna(r.route_note) else r.route_note}
    return by_country, c


def fixture_hashes() -> dict:
    names = ["steel_facility_months.csv", "grid_intensity_ember_annual.csv", "cbam_defaults_steel.csv", "countries.csv"]
    return {n: F.sha256_file(F.FIXTURES / n) for n in names}


def build_artefact(df: pd.DataFrame, hp: dict, rounds: int, seed: int = FINAL_SEED) -> tuple[bytes, list[lgb.Booster], dict]:
    """Fit on train plants, calibrate on calibration plants, serialise. Pure function of (fixtures, hp, rounds, seed)."""
    tr = df[df["split"] == "train"]
    cal = df[df["split"] == "calib"]
    boosters = fit_triplet(tr[F.FEATURES], tr[F.TARGET].to_numpy(), hp, rounds, seed)
    Pcal = predict_sorted(boosters, cal[F.FEATURES])
    # per-route offsets only for routes with >= 30 calibration plants; the rest use the pooled offsets
    n_plants = cal.groupby("route")["source_id"].nunique()
    big = [r for r, n in n_plants.items() if n >= 30]
    offs_all = conformal_offsets(cal[F.TARGET].to_numpy(), Pcal, cal["route"].to_numpy())
    offsets = {"ALL": offs_all["ALL"]} | {r: offs_all[r] for r in big}
    for r in offsets:
        offsets[r]["n_calibration_plants"] = int(cal["source_id"].nunique() if r == "ALL" else n_plants[r])

    table = F.load_grid_table()
    countries = pd.read_csv(F.FIXTURES / "countries.csv")
    cbam, _ = cbam_tables()
    artefact = {
        "schema": "embedproof.model.v1",
        "description": "LightGBM quantile regression (q05/q50/q95) of specific embedded emissions (direct+indirect, tCO2 per t steel); "
                       "target = Climate TRACE v5.10.0 modelled emissions factor, NOT a measurement.",
        "target": {"name": F.TARGET, "unit": "tCO2e per tonne of steel", "definition": "Climate TRACE iron-and-steel `other1` (direct + indirect emissions factor)"},
        "lightgbm_version": lgb.__version__,
        "features": [
            {"name": "route_code", "label": F.FEATURE_LABELS["route_code"], "levels": F.ROUTE_CODES, "kind": "ordinal-coded categorical"},
            {"name": "capacity_tpy", "label": F.FEATURE_LABELS["capacity_tpy"], "kind": "numeric"},
            {"name": "grid_gco2_kwh", "label": F.FEATURE_LABELS["grid_gco2_kwh"], "kind": "numeric", "source": "Ember yearly CO2 intensity, last observation carried forward"},
        ],
        "quantiles": list(QUANTILES),
        "hyperparameters": dict(BASE_PARAMS) | hp | {"rounds": rounds, "seed": seed},
        "post_processing": {
            "crossing_fix": "sort the three predictions per row (monotone rearrangement)",
            "calibration": "one-sided split-conformal offsets on calibration plants: q05' = max(0, min(q05 - lower, q50)); q95' = max(q95 + upper, q50)",
            "offsets": offsets,
        },
        "boosters": {"q05": boosters[0].model_to_string(), "q50": boosters[1].model_to_string(), "q95": boosters[2].model_to_string()},
        "cbam_defaults": {
            "source": "European Commission, CBAM default values (definitive period) Excel, version 2 dated 2026-08-06: Annex I of Implementing "
                      "Regulation (EU) 2025/2621 as corrected by (EU) 2026/1740. Informational copy; only the Official Journal text is legally binding.",
            "scope_note": "For iron and steel the EC workbook gives direct emissions only (indirect = N/A); total == direct. Compared against a direct+indirect SEE, "
                          "so the 0.5x-default rule is slightly conservative.",
            "assumption_A_CBAM_1": "Defaults are per CN code and exporting country, not per route. One representative CN code per route: BF-BOF=7208, EAF=7214 20 00, DRI-EAF=7207 11 14.",
            "fallback": "Countries without a steel default on their own sheet (and EU members) use the '_Other Countries and Territories' sheet, key OTHER.",
            "markup_note": "Values as published in the workbook; any year-dependent mark-up (10 % in 2026) is NOT added.",
            "by_country": cbam,
        },
        "grid_intensity_ember": {str(k): {str(y): v for y, v in sorted(s.items())} for k, s in sorted(table.items())},
        "iso2_to_iso3": dict(zip(countries["iso2"].fillna("NA"), countries["iso3"])),
        "training_ranges": {
            r: {"capacity_tpy": [float(g["capacity_tpy"].min()), float(g["capacity_tpy"].max())], "plants": int(g["source_id"].nunique()),
                "grid_gco2_kwh": [float(g["grid_gco2_kwh"].min()), float(g["grid_gco2_kwh"].max())]}
            for r, g in tr.groupby("route")},
        "training_data": {
            "snapshot_sha256": F.sha256_file(F.FIXTURES / "steel_facility_months.csv"),
            "fixture_sha256": fixture_hashes(),
            "source": "Climate TRACE v5.10.0 iron-and-steel sources CSV (CC BY 4.0), Ember yearly electricity data (CC BY 4.0)",
            "rows_by_split": {k: int(v) for k, v in df["split"].value_counts().sort_index().items()},
            "plants_by_split": {k: int(v) for k, v in df.groupby("split")["source_id"].nunique().sort_index().items()},
            "split_seed": SPLIT_SEED,
        },
    }
    blob = (json.dumps(artefact, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    return blob, boosters, artefact


# ----------------------------------------------------------------------------- evaluation
def cluster_bootstrap(plant_ids: np.ndarray, stats: dict[str, np.ndarray], fn, n: int = BOOTSTRAPS, seed: int = 1) -> tuple[float, float]:
    """95 % CI by resampling PLANTS with replacement. `stats` are per-row arrays aggregated per plant (sums)."""
    uniq, inv = np.unique(plant_ids, return_inverse=True)
    agg = {k: np.bincount(inv, weights=v.astype(float), minlength=len(uniq)) for k, v in stats.items()}
    rng = np.random.RandomState(seed)
    vals = []
    for _ in range(n):
        idx = rng.randint(0, len(uniq), len(uniq))
        vals.append(fn({k: v[idx].sum() for k, v in agg.items()}))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def evaluate(df: pd.DataFrame, boosters: list[lgb.Booster], artefact: dict) -> dict:
    tr = df[df["split"] == "train"]
    te = df[df["split"] == "test"].reset_index(drop=True)
    offsets = artefact["post_processing"]["offsets"]
    y = te[F.TARGET].to_numpy()
    routes = te["route"].to_numpy()
    Praw = predict_sorted(boosters, te[F.FEATURES])
    P = apply_calibration(Praw, routes, offsets)
    P = np.round(P, 4)
    q05, q50, q95 = P[:, 0], P[:, 1], P[:, 2]

    # baseline: median SEE of the training plants' rows of the same route
    base_med = tr.groupby("route")[F.TARGET].median().to_dict()
    base = te["route"].map(base_med).to_numpy()
    ae_m, ae_b = np.abs(y - q50), np.abs(y - base)
    cov = (y >= q05) & (y <= q95)
    cov_raw = (y >= Praw[:, 0]) & (y <= Praw[:, 2])
    pid = te["source_id"].to_numpy()
    ci_impr = cluster_bootstrap(pid, {"m": ae_m, "b": ae_b, "n": np.ones(len(y))}, lambda s: 1 - s["m"] / s["b"])
    ci_cov = cluster_bootstrap(pid, {"c": cov, "n": np.ones(len(y))}, lambda s: s["c"] / s["n"])
    ci_mae = cluster_bootstrap(pid, {"m": ae_m, "n": np.ones(len(y))}, lambda s: s["m"] / s["n"])
    # equal weight per plant (a plant with 66 months should not outweigh one with 40)
    per_plant = pd.DataFrame({"pid": pid, "cov": cov, "ae_m": ae_m, "ae_b": ae_b}).groupby("pid").mean()

    point = {
        "n_test_plants": int(te["source_id"].nunique()), "n_test_rows": int(len(te)),
        "mae_q50": float(ae_m.mean()), "mae_q50_ci95_plant_bootstrap": ci_mae,
        "mae_route_median_baseline": float(ae_b.mean()),
        "improvement_vs_baseline": float(1 - ae_m.mean() / ae_b.mean()), "improvement_ci95_plant_bootstrap": ci_impr,
        "improvement_vs_baseline_equal_weight_per_plant": float(1 - per_plant["ae_m"].mean() / per_plant["ae_b"].mean()),
        "route_median_baseline_values": {k: float(v) for k, v in base_med.items()},
        "coverage_90_calibrated_rows": float(cov.mean()), "coverage_ci95_plant_bootstrap": ci_cov,
        "coverage_90_calibrated_equal_weight_per_plant": float(per_plant["cov"].mean()),
        "coverage_90_before_conformal": float(cov_raw.mean()),
        "mean_interval_width": float((q95 - q05).mean()),
        "target_check": {"mae_improvement_ge_20pct": bool(1 - ae_m.mean() / ae_b.mean() >= 0.20),
                         "coverage_in_85_95": bool(0.85 <= cov.mean() <= 0.95)},
    }

    by_route, by_method = {}, {}
    for r in sorted(set(routes)):
        m = routes == r
        by_route[r] = {"plants": int(te.loc[m, "source_id"].nunique()), "rows": int(m.sum()), "mae_q50": float(ae_m[m].mean()),
                       "mae_baseline": float(ae_b[m].mean()), "improvement": float(1 - ae_m[m].mean() / ae_b[m].mean()),
                       "coverage_90": float(cov[m].mean()), "mean_width": float((q95 - q05)[m].mean())}
    for k in sorted(te["ct_method"].unique()):
        m = (te["ct_method"] == k).to_numpy()
        by_method[k] = {"rows": int(m.sum()), "mae_q50": float(ae_m[m].mean()), "mae_baseline": float(ae_b[m].mean()),
                        "coverage_90": float(cov[m].mean())}

    # ---- detection on SYNTHETIC underreports: declared = factor x Climate TRACE truth (labelled synthetic everywhere)
    cb, _ = cbam_tables()
    def cbam_default(iso3, route):
        return (cb.get(iso3) or cb["OTHER"]).get(route, cb["OTHER"][route])["total"]
    dflt = np.array([cbam_default(i, r) for i, r in zip(te["iso3"], routes)])
    det = {}
    for f in (*DETECTION_FACTORS, 1.0):
        decl = np.round(f * y, 6)
        bh = (decl < CBAM_HALF * dflt).astype(float)
        r_full = risk_vec(q05, q50, decl, bh)
        r_int = risk_vec(q05, q50, decl, np.zeros(len(y)))
        red, red_i = (r_full > RED), (r_int > RED)
        key = f"{f:g}x"
        det[key] = {
            "synthetic": f != 1.0,
            "red_rate_full_rule": float(red.mean()), "red_rate_interval_only": float(red_i.mean()),
            "red_rate_full_rule_ci95_plant_bootstrap": cluster_bootstrap(pid, {"c": red, "n": np.ones(len(y))}, lambda s: s["c"] / s["n"]),
            "red_rate_interval_only_ci95_plant_bootstrap": cluster_bootstrap(pid, {"c": red_i, "n": np.ones(len(y))}, lambda s: s["c"] / s["n"]),
            "amber_or_red_rate_full_rule": float((r_full > AMBER).mean()),
            "mean_risk_full_rule": float(r_full.mean()),
            "below_half_cbam_default_rate": float(bh.mean()),
            "by_route_red_rate_full_rule": {r: float(red[routes == r].mean()) for r in sorted(set(routes))},
            "by_route_red_rate_interval_only": {r: float(red_i[routes == r].mean()) for r in sorted(set(routes))},
        }
    det["note"] = ("Rows with factor < 1 are SYNTHETIC underreports built from Climate TRACE's own estimate; '1x' is the false-alert rate on "
                   "unmodified held-out values. recall == red_rate for factor < 1. 'full_rule' is the shipped risk score (incl. +20 under "
                   "0.5x CBAM default); 'interval_only' drops the +20 rule.")
    det["targets"] = {"recall_0.5x_ge_0.9": bool(det["0.5x"]["red_rate_full_rule"] >= 0.9),
                      "recall_0.7x_ge_0.6": bool(det["0.7x"]["red_rate_full_rule"] >= 0.6)}
    return {"point_accuracy_and_coverage": point, "by_route": by_route, "by_climate_trace_method": by_method, "detection_synthetic": det}


def shap_stability(df: pd.DataFrame, hp: dict, rounds: int, ref_boosters: list[lgb.Booster]) -> dict:
    tr = df[df["split"] == "train"]
    te = df[df["split"] == "test"].sample(n=3000, random_state=0, replace=False)
    Xte = te[F.FEATURES]

    def importance(b):
        sv = shap.TreeExplainer(b).shap_values(Xte)
        m = np.abs(sv).mean(axis=0)
        return m, [F.FEATURES[i] for i in np.argsort(-m)]

    ref_m, ref_rank = importance(ref_boosters[1])
    runs = {}
    for s in SHAP_SEEDS:
        b = fit_booster(tr[F.FEATURES], tr[F.TARGET].to_numpy(), 0.5, hp, rounds, s)
        m, rank = importance(b)
        runs[str(s)] = {"rank": rank, "mean_abs_shap": dict(zip(F.FEATURES, map(float, m)))}
    ranks = [tuple(v["rank"]) for v in runs.values()]
    return {
        "note": "The model has only 3 features, so the top-3 SET is trivially identical; stability of the RANK ORDER is the informative check.",
        "reference_seed": FINAL_SEED, "reference_rank": ref_rank,
        "reference_mean_abs_shap": dict(zip(F.FEATURES, map(float, ref_m))),
        "seeds": runs,
        "top3_set_identical_in_all_seeds": bool(len({frozenset(r) for r in ranks}) == 1),
        "rank_order_identical_in_all_seeds": bool(len(set(ranks)) == 1),
        "rank_order_matches_reference_in": int(sum(tuple(r) == tuple(ref_rank) for r in ranks)),
        "n_seeds": len(SHAP_SEEDS),
    }


ABLATION_VARIANTS = {
    "route only": ["route_code"],
    "route + grid": ["route_code", "grid_gco2_kwh"],
    "route + capacity": ["route_code", "capacity_tpy"],
    "route + capacity + grid  (SHIPPED)": F.FEATURES,
    "route + capacity + grid + country (categorical)": F.FEATURES + ["iso3"],
    "route + capacity + grid + Climate TRACE capacity factor  (EXCLUDED a priori: leakage proxy)": F.FEATURES + ["ct_capacity_factor"],
}


def ablation(tr: pd.DataFrame, hp: dict, rounds: int, names: list[str] | None = None) -> dict:
    out = {}
    for name, feats in ABLATION_VARIANTS.items():
        if names and name not in names:
            continue
        r = grouped_cv(tr, feats, hp, rounds_list=(rounds,))[rounds]
        out[name] = {k: round(v, 5) for k, v in r.items()}
    return out


def country_holdout(tr: pd.DataFrame, hp: dict, rounds: int) -> dict:
    """Leave-countries-out CV on the TRAIN plants: how the shipped feature set behaves in a country the model has never seen."""
    r = grouped_cv(tr, F.FEATURES, hp, rounds_list=(rounds,), group_col="iso3")[rounds]
    r = {k: round(v, 5) for k, v in r.items()}
    r["improvement_vs_baseline"] = round(1 - r["mae_q50"] / r["mae_route_median_baseline"], 4)
    r["note"] = "5 folds grouped by country; coverage_raw is BEFORE conformal calibration"
    return r


def measure_latency(n: int = 300) -> dict:
    from model.scorer import Scorer
    s = Scorer.load()
    rep = json.loads((F.REPO / "data" / "demo_reports" / "honest_eaf.json").read_text("utf-8"))
    inst = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 100000, "country": "TR"}
    s.score_report(rep, inst)  # warm-up
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        s.score_report(rep, inst)
        t.append((time.perf_counter() - t0) * 1000)
    t = np.array(t)
    return {"n_calls": n, "mean_ms": float(t.mean()), "p50_ms": float(np.percentile(t, 50)), "p95_ms": float(np.percentile(t, 95)),
            "max_ms": float(t.max()), "includes": "feature build + 3 boosters + SHAP TreeExplainer(q50) + explanation, warm process"}


# ----------------------------------------------------------------------------- main
def write_eval(ev: dict) -> None:
    with open(EVAL_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ev, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-selection", action="store_true", help="reuse hyper-parameters stored in the existing artefact")
    ap.add_argument("--skip-extras", action="store_true", help="skip ablation / SHAP seeds (still writes artefact + hash)")
    ap.add_argument("--latency-only", action="store_true", help="re-measure scorer latency and merge it into eval.json")
    args = ap.parse_args()
    t_start = time.time()
    if args.latency_only:
        ev = json.loads(EVAL_PATH.read_text("utf-8"))
        ev["latency"] = measure_latency()
        write_eval(ev)
        print(json.dumps(ev["latency"], indent=1))
        return

    df = make_split(F.load_training_frame())
    tr = df[df["split"] == "train"].reset_index(drop=True)
    print(f"rows {len(df)}  plants {df['source_id'].nunique()}  split plants "
          f"{df.groupby('split')['source_id'].nunique().to_dict()}")

    # ---- 3. hyper-parameter selection (train plants only) -------------------------------------------------
    selection = []
    if args.skip_selection and ARTEFACT_PATH.exists():
        h = json.loads(ARTEFACT_PATH.read_text("utf-8"))["hyperparameters"]
        best = {"hp": {"num_leaves": h["num_leaves"], "min_data_in_leaf": h["min_data_in_leaf"]}, "rounds": h["rounds"]}
    else:
        best = None
        for hp in HP_GRID:
            res = grouped_cv(tr, F.FEATURES, hp)
            for r, m in res.items():
                selection.append({"hp": hp, "rounds": r} | {k: round(v, 5) for k, v in m.items()})
                if best is None or m["pinball"] < best["pinball"] - 1e-9:
                    best = {"hp": hp, "rounds": r, "pinball": m["pinball"]}
            print("cv", hp, {r: round(m["pinball"], 5) for r, m in res.items()}, f"{time.time()-t_start:.0f}s", flush=True)
    hp, rounds = best["hp"], best["rounds"]
    print("selected", hp, rounds)

    # ---- 4/5. fit, calibrate, freeze (twice: must be byte-identical) ----------------------------------------
    blob1, boosters, artefact = build_artefact(df, hp, rounds)
    blob2, _, _ = build_artefact(df, hp, rounds)
    h1, h2 = hashlib.sha256(blob1).hexdigest(), hashlib.sha256(blob2).hexdigest()
    assert h1 == h2, f"training is not deterministic: {h1} != {h2}"
    ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTEFACT_PATH.write_bytes(blob1)
    version = "0x" + h1
    HASH_PATH.write_bytes(version.encode("ascii"))  # exactly the hash string, no trailing newline
    print(f"artefact {ARTEFACT_PATH.name}  {len(blob1)/1e6:.2f} MB  determinism check: two builds identical")

    # ---- 6. evaluate ---------------------------------------------------------------------------------------
    ev = evaluate(df, boosters, artefact)
    ev_out = {
        "modelVersionHash": version,
        "evaluated_artefact": "trained on the train-split plants only; the test plants were never used for any decision",
        "target_note": "Truth = Climate TRACE v5.10.0 modelled direct+indirect emissions factor. Metrics measure agreement with that model, not with metered plant emissions.",
        "data": {"fixture_sha256": artefact["training_data"]["fixture_sha256"], "rows_by_split": artefact["training_data"]["rows_by_split"],
                 "plants_by_split": artefact["training_data"]["plants_by_split"]},
        "dataset": {
            "rows": int(len(df)), "plants": int(df["source_id"].nunique()), "countries": int(df["iso3"].nunique()),
            "months": [int(df["year"].min()), int(df["year"].max())],
            "plants_by_route_and_split": {f"{a}/{b}": int(n) for (a, b), n in df.groupby(["route", "split"])["source_id"].nunique().items()},
            "rows_with_ember_grid_missing": int(df["grid_gco2_kwh"].isna().sum()),
        },
        "training_ranges_train_plants": artefact["training_ranges"],
        "hyperparameters": artefact["hyperparameters"],
        "calibration_offsets": artefact["post_processing"]["offsets"],
        "cv_selection_train_plants_only": selection,
    } | ev
    if not args.skip_extras:
        # ablation on the TRAIN plants (grouped CV) with the selected hyper-parameters
        ev_out["feature_ablation_grouped_cv_train_plants"] = ablation(tr, hp, rounds)
        # the same two-way comparison with bigger trees, to show what the excluded proxy does to a more flexible model
        big = {"num_leaves": 15, "min_data_in_leaf": 200}
        ev_out["feature_ablation_larger_trees_grouped_cv_train_plants"] = {
            "hyperparameters": big | {"rounds": 200},
            "results": ablation(tr, big, 200, names=["route + capacity + grid  (SHIPPED)",
                                                     "route + capacity + grid + Climate TRACE capacity factor  (EXCLUDED a priori: leakage proxy)"])}
        ev_out["leave_country_out_cv_train_plants"] = country_holdout(tr, hp, rounds)
        ev_out["shap_stability"] = shap_stability(df, hp, rounds, boosters)
    write_eval(ev_out)
    print(json.dumps({k: ev_out[k] for k in ("point_accuracy_and_coverage",)}, indent=1))
    print(json.dumps(ev_out["detection_synthetic"], indent=1)[:3000])

    try:
        ev_out["latency"] = measure_latency()
        write_eval(ev_out)
        print("latency", ev_out["latency"])
    except (ImportError, FileNotFoundError) as e:  # scorer / demo reports not written yet on the very first run
        print("latency skipped:", e)
    print(f"\nmodelVersionHash {version}\n({time.time()-t_start:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
