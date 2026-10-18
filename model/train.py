"""Train, freeze and evaluate the EmbedProof plausibility model, round 2 (LightGBM quantile regression, lagged Climate TRACE prior).

Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe -m model.train

Pipeline
  1. Load data/fixtures/steel_facility_months.csv (Climate TRACE v5.10.0 plant-months) + Ember grid intensity, and add the
     LAGGED prior: each plant-month gets the mean of the plant's OWN Climate TRACE direct emissions factor over months m-14..m-3
     (NaN with < 6 months of history).
  2. Split PLANTS (never rows) 58 / 22 / 20 % into train / calibration / test, stratified by route.
  3. Choose hyper-parameters by 5-fold plant-grouped CV on the train plants only. In training, the prior of a random 30 % of
     plants is blanked (all months) so the model also learns the no-history case. The test plants are not touched until step 6.
  4. Fit q = 0.05 / 0.50 / 0.95 boosters on the train plants; sort the three predictions per row (removes quantile crossing); fit
     one-sided split-conformal offsets PER ROUTE and PER REGIME (prior present / prior absent) on the calibration plants.
  5. Freeze everything into ONE json file (model/artefacts/embedproof_model_v1.json);
     modelVersionHash = "0x" + sha256(file bytes). Training is run twice and must give identical bytes.
  6. Evaluate once on the held-out test plants -> model/artefacts/eval.json (+ SHAP stability, latency).

HONESTY NOTE: the ground truth is Climate TRACE's own modelled emissions factor and the prior is Climate TRACE's own history of
the same plant, so the "with prior" numbers measure consistency with that history (persistence), not real-world accuracy.

Everything is deterministic (deterministic=True, force_row_wise, 1 thread, fixed seeds).
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
# Round 2 uses a NEW plant split: the round-1 test plants were already scored once, so a fresh seed gives a test set that no
# round-2 decision (features, prior window, mask fraction, hyper-parameters, calibration) has ever seen.
SPLIT_SEED = 20261019
TEST_FRAC, CAL_FRAC = 0.20, 0.22
FINAL_SEED = 7
MASK_FRAC, MASK_SEED = 0.30, 11
SHAP_SEEDS = (101, 102, 103, 104, 105)
BASE_PARAMS = dict(objective="quantile", learning_rate=0.05, bagging_fraction=0.8, bagging_freq=1, max_bin=255,
                   verbose=-1, num_threads=1, deterministic=True, force_row_wise=True)
HP_GRID = [dict(num_leaves=nl, min_data_in_leaf=md) for nl in (7, 15, 31) for md in (100, 300)]
ROUNDS = (300, 600)
DETECTION_FACTORS = (0.5, 0.7, 0.85)
RED, AMBER = 60, 30
CBAM_HALF = 0.5
BOOTSTRAPS = 2000


# ----------------------------------------------------------------------------- data / splits
def make_split(df: pd.DataFrame) -> pd.DataFrame:
    """Plant-level split, stratified by route: TEST_FRAC test, CAL_FRAC calibration, rest train."""
    rng = np.random.RandomState(SPLIT_SEED)
    plants = df.groupby("source_id")["route"].first().reset_index().sort_values("source_id")
    split: dict[int, str] = {}
    for _, g in plants.groupby("route", sort=True):
        ids = g["source_id"].to_numpy().copy()
        rng.shuffle(ids)
        n = len(ids)
        n_test, n_cal = int(round(TEST_FRAC * n)), int(round(CAL_FRAC * n))
        for i in ids[:n_test]:
            split[int(i)] = "test"
        for i in ids[n_test:n_test + n_cal]:
            split[int(i)] = "calib"
        for i in ids[n_test + n_cal:]:
            split[int(i)] = "train"
    df = df.copy()
    df["split"] = df["source_id"].map(split)
    return df


def mask_prior(df: pd.DataFrame, frac: float = MASK_FRAC, seed: int = MASK_SEED) -> pd.DataFrame:
    """Blank the Climate TRACE prior of a random `frac` of the plants (all their months): teaches the no-history branch."""
    ids = np.sort(df["source_id"].unique())
    chosen = np.random.RandomState(seed).permutation(ids)[: int(round(frac * len(ids)))]
    out = df.copy()
    out.loc[out["source_id"].isin(chosen), "ct_prior_ef"] = np.nan
    return out


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


def _cv_metrics(y, P, base):
    return {"pinball": mean_pinball(y, P), "mae_q50": float(np.mean(np.abs(y - P[:, 1]))),
            "mae_route_median_baseline": float(np.mean(np.abs(y - base))),
            "coverage_raw": float(np.mean((y >= P[:, 0]) & (y <= P[:, 2]))), "rows": int(len(y))}


def grouped_cv(df: pd.DataFrame, feats: list[str], hp: dict, rounds_list=ROUNDS, seed: int = FINAL_SEED,
               group_col: str = "source_id") -> dict:
    """5-fold grouped CV on the TRAIN plants (groups = plants by default, countries for the leave-country-out check).

    Inside each fold the training plants get the same random prior-blanking as the real fit. Validation is scored twice:
    `with_prior` = validation rows that have a real prior, as they are; `no_prior` = ALL validation rows with the prior blanked.
    The route-median baseline is fitted inside each training fold.
    """
    has_prior = "ct_prior_ef" in feats
    y = df[F.TARGET].to_numpy()
    out_w = {r: np.zeros((len(df), 3)) for r in rounds_list}
    out_m = {r: np.zeros((len(df), 3)) for r in rounds_list}
    base = np.zeros(len(df))
    for k, (tr, va) in enumerate(GroupKFold(n_splits=5).split(df, y, df[group_col])):
        dtr, dva = df.iloc[tr], df.iloc[va]
        if has_prior:
            dtr = mask_prior(dtr, MASK_FRAC, MASK_SEED + k)
        base[va] = dva["route"].map(df.iloc[tr].groupby("route")[F.TARGET].median()).to_numpy()
        Xva_w = dva[feats]
        Xva_m = Xva_w.assign(ct_prior_ef=np.nan) if has_prior else Xva_w
        for j, a in enumerate(QUANTILES):
            b = fit_booster(dtr[feats], dtr[F.TARGET].to_numpy(), a, hp, max(rounds_list), seed)
            for r in rounds_list:
                out_w[r][va, j] = b.predict(Xva_w, num_iteration=r)
                out_m[r][va, j] = b.predict(Xva_m, num_iteration=r)
    ok = df["ct_prior_ef"].notna().to_numpy() if has_prior else np.ones(len(df), dtype=bool)
    res = {}
    for r in rounds_list:
        Pw, Pm = np.sort(out_w[r], axis=1), np.sort(out_m[r], axis=1)
        w, m = _cv_metrics(y[ok], Pw[ok], base[ok]), _cv_metrics(y, Pm, base)
        res[r] = {"with_prior": w, "no_prior": m, "selection_score": 0.5 * (w["pinball"] + m["pinball"])}
    return res


# ----------------------------------------------------------------------------- calibration + risk
def regime_frames(cal: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"with_prior": cal[cal["ct_prior_ef"].notna()], "no_prior": cal.assign(ct_prior_ef=np.nan)}


def conformal_offsets(boosters: list[lgb.Booster], cal: pd.DataFrame) -> dict:
    """One-sided split-conformal offsets, PER ROUTE and PER REGIME, from the calibration plants only.

    lower = 95th percentile of (q05 - y)  -> q05' = q05 - lower     (each tail then holds ~5 % of calibration rows)
    upper = 95th percentile of (y - q95)  -> q95' = q95 + upper
    """
    out: dict = {}
    for regime, frame in regime_frames(cal).items():
        P = predict_sorted(boosters, frame[F.FEATURES])
        y = frame[F.TARGET].to_numpy()
        out[regime] = {}
        for r in sorted(frame["route"].unique()):
            m = (frame["route"] == r).to_numpy()
            out[regime][r] = {
                "lower": float(np.quantile(P[m, 0] - y[m], 0.95, method="higher")),
                "upper": float(np.quantile(y[m] - P[m, 2], 0.95, method="higher")),
                "n_rows": int(m.sum()), "n_calibration_plants": int(frame.loc[m, "source_id"].nunique())}
    return out


def apply_calibration(P: np.ndarray, routes: np.ndarray, offsets: dict) -> np.ndarray:
    lo = np.array([P[i, 0] - offsets[r]["lower"] for i, r in enumerate(routes)])
    hi = np.array([P[i, 2] + offsets[r]["upper"] for i, r in enumerate(routes)])
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
    """Fit on train plants (30 % of them with the prior blanked), calibrate on calibration plants, serialise.

    Pure function of (fixtures, hp, rounds, seed).
    """
    tr = df[df["split"] == "train"]
    cal = df[df["split"] == "calib"]
    trm = mask_prior(tr)
    boosters = fit_triplet(trm[F.FEATURES], trm[F.TARGET].to_numpy(), hp, rounds, seed)
    offsets = conformal_offsets(boosters, cal)

    table = F.load_grid_table()
    countries = pd.read_csv(F.FIXTURES / "countries.csv")
    cbam, _ = cbam_tables()
    artefact = {
        "schema": "embedproof.model.v1",
        "description": "LightGBM quantile regression (q05/q50/q95) of specific embedded emissions (direct+indirect, tCO2e per t steel); "
                       "target = Climate TRACE v5.10.0 modelled emissions factor, NOT a measurement. Inputs include a LAGGED Climate TRACE "
                       "prior of the same plant, so scores measure consistency with Climate TRACE's own history (persistence).",
        "target": {"name": F.TARGET, "unit": "tCO2e per tonne of steel", "definition": "Climate TRACE iron-and-steel `other1` (direct + indirect emissions factor)"},
        "lightgbm_version": lgb.__version__,
        "features": [
            {"name": "route_code", "label": F.FEATURE_LABELS["route_code"], "levels": F.ROUTE_CODES, "kind": "ordinal-coded categorical"},
            {"name": "capacity_tpy", "label": F.FEATURE_LABELS["capacity_tpy"], "kind": "numeric"},
            {"name": "grid_gco2_kwh", "label": F.FEATURE_LABELS["grid_gco2_kwh"], "kind": "numeric", "source": "Ember yearly CO2 intensity, last observation carried forward"},
            {"name": "ct_prior_ef", "label": F.FEATURE_LABELS["ct_prior_ef"], "kind": "numeric, NaN = no history",
             "definition": "mean of the plant's own Climate TRACE direct emissions factor over months m-14..m-3 (>= 6 months present)",
             "window": {"lag_end": F.PRIOR_LAG_END, "lag_start": F.PRIOR_LAG_START, "min_months": F.PRIOR_MIN_MONTHS}},
        ],
        "quantiles": list(QUANTILES),
        "hyperparameters": dict(BASE_PARAMS) | hp | {"rounds": rounds, "seed": seed, "prior_mask_fraction": MASK_FRAC, "prior_mask_seed": MASK_SEED},
        "post_processing": {
            "crossing_fix": "sort the three predictions per row (monotone rearrangement)",
            "calibration": "one-sided split-conformal offsets per route and per regime (with_prior / no_prior) on calibration plants: "
                           "q05' = max(0, min(q05 - lower, q50)); q95' = max(q95 + upper, q50)",
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
                "grid_gco2_kwh": [float(g["grid_gco2_kwh"].min()), float(g["grid_gco2_kwh"].max())],
                "ct_prior_ef": [float(g["ct_prior_ef"].min()), float(g["ct_prior_ef"].max())]}
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


def regime_block(sub: pd.DataFrame, Praw: np.ndarray, offsets: dict, base_med: dict, cbam: dict) -> dict:
    """Accuracy, coverage and synthetic-underreport detection of one regime on the rows `sub` (never used for any decision)."""
    sub = sub.reset_index(drop=True)
    y = sub[F.TARGET].to_numpy()
    routes = sub["route"].to_numpy()
    pid = sub["source_id"].to_numpy()
    P = np.round(apply_calibration(Praw, routes, offsets), 4)
    q05, q50, q95 = P[:, 0], P[:, 1], P[:, 2]
    base = sub["route"].map(base_med).to_numpy()
    ae_m, ae_b = np.abs(y - q50), np.abs(y - base)
    cov = (y >= q05) & (y <= q95)
    ones = np.ones(len(y))
    per_plant = pd.DataFrame({"pid": pid, "cov": cov, "ae_m": ae_m, "ae_b": ae_b}).groupby("pid").mean()
    point = {
        "n_rows": int(len(y)), "n_plants": int(sub["source_id"].nunique()),
        "mae_q50": float(ae_m.mean()), "mae_q50_ci95_plant_bootstrap": cluster_bootstrap(pid, {"m": ae_m, "n": ones}, lambda s: s["m"] / s["n"]),
        "mae_route_median_baseline": float(ae_b.mean()),
        "improvement_vs_baseline": float(1 - ae_m.mean() / ae_b.mean()),
        "improvement_ci95_plant_bootstrap": cluster_bootstrap(pid, {"m": ae_m, "b": ae_b, "n": ones}, lambda s: 1 - s["m"] / s["b"]),
        "improvement_vs_baseline_equal_weight_per_plant": float(1 - per_plant["ae_m"].mean() / per_plant["ae_b"].mean()),
        "coverage_90": float(cov.mean()), "coverage_ci95_plant_bootstrap": cluster_bootstrap(pid, {"c": cov, "n": ones}, lambda s: s["c"] / s["n"]),
        "coverage_90_equal_weight_per_plant": float(per_plant["cov"].mean()),
        "coverage_90_before_conformal": float(((y >= Praw[:, 0]) & (y <= Praw[:, 2])).mean()),
        "mean_interval_width": float((q95 - q05).mean()),
        "target_check": {"mae_improvement_ge_20pct": bool(1 - ae_m.mean() / ae_b.mean() >= 0.20),
                         "coverage_in_85_95": bool(0.85 <= cov.mean() <= 0.95)},
    }
    by_route = {}
    for r in sorted(set(routes)):
        m = routes == r
        by_route[r] = {"plants": int(sub.loc[m, "source_id"].nunique()), "rows": int(m.sum()), "mae_q50": float(ae_m[m].mean()),
                       "mae_baseline": float(ae_b[m].mean()), "improvement": float(1 - ae_m[m].mean() / ae_b[m].mean()),
                       "coverage_90": float(cov[m].mean()), "mean_width": float((q95 - q05)[m].mean())}

    def cbam_default(iso3, route):
        return (cbam.get(iso3) or cbam["OTHER"]).get(route, cbam["OTHER"][route])["total"]
    dflt = np.array([cbam_default(i, r) for i, r in zip(sub["iso3"], routes)])
    det = {}
    for f in (*DETECTION_FACTORS, 1.0):
        decl = np.round(f * y, 6)
        bh = (decl < CBAM_HALF * dflt).astype(float)
        r_full = risk_vec(q05, q50, decl, bh)
        r_int = risk_vec(q05, q50, decl, np.zeros(len(y)))
        red, red_i = (r_full > RED), (r_int > RED)
        det[f"{f:g}x"] = {
            "synthetic": f != 1.0,
            "red_rate_full_rule": float(red.mean()),
            "red_rate_full_rule_ci95_plant_bootstrap": cluster_bootstrap(pid, {"c": red, "n": ones}, lambda s: s["c"] / s["n"]),
            "red_rate_interval_only": float(red_i.mean()),
            "amber_or_red_rate_full_rule": float((r_full > AMBER).mean()),
            "mean_risk_full_rule": float(r_full.mean()),
            "below_half_cbam_default_rate": float(bh.mean()),
            "by_route_red_rate_full_rule": {r: float(red[routes == r].mean()) for r in sorted(set(routes))},
            "by_route_red_rate_interval_only": {r: float(red_i[routes == r].mean()) for r in sorted(set(routes))},
        }
    det["targets"] = {"recall_0.5x_ge_0.9": bool(det["0.5x"]["red_rate_full_rule"] >= 0.9),
                      "recall_0.7x_ge_0.6": bool(det["0.7x"]["red_rate_full_rule"] >= 0.6)}
    return {"point_accuracy_and_coverage": point, "by_route": by_route, "detection_synthetic": det}


def evaluate(df: pd.DataFrame, boosters: list[lgb.Booster], artefact: dict) -> dict:
    tr = df[df["split"] == "train"]
    te = df[df["split"] == "test"].reset_index(drop=True)
    offs = artefact["post_processing"]["offsets"]
    base_med = tr.groupby("route")[F.TARGET].median().to_dict()
    cb, _ = cbam_tables()
    has = te["ct_prior_ef"].notna().to_numpy()
    te_a = te[has]                                   # rows with a real prior (as a verifier would have it)
    te_b_same = te_a.assign(ct_prior_ef=np.nan)      # the SAME rows, prior withheld -> like-for-like comparison
    te_b_all = te.assign(ct_prior_ef=np.nan)         # every test row, prior withheld (includes the natural no-history rows)

    def raw(frame):
        return predict_sorted(boosters, frame[F.FEATURES])

    out = {
        "test_set": {"plants": int(te["source_id"].nunique()), "rows": int(len(te)),
                     "rows_with_prior": int(has.sum()), "plants_with_prior": int(te_a["source_id"].nunique()),
                     "rows_without_prior_naturally": int((~has).sum()),
                     "route_median_baseline_values": {k: float(v) for k, v in base_med.items()}},
        "with_prior": regime_block(te_a, raw(te_a), offs["with_prior"], base_med, cb),
        "without_prior_same_rows": regime_block(te_b_same, raw(te_b_same), offs["no_prior"], base_med, cb),
        "without_prior_all_test_rows": regime_block(te_b_all, raw(te_b_all), offs["no_prior"], base_med, cb),
    }
    # reference: plain persistence of the plant's own lagged direct+indirect SEE (no model at all), same rows as `with_prior`
    y = te_a[F.TARGET].to_numpy()
    pers = te_a["hist_see_mean"].to_numpy()
    base = te_a["route"].map(base_med).to_numpy()
    out["persistence_reference"] = {
        "definition": "predict SEE(m) = mean of the same plant's Climate TRACE SEE over m-14..m-3; no model, no interval",
        "mae": float(np.mean(np.abs(y - pers))), "mae_route_median_baseline": float(np.mean(np.abs(y - base))),
        "mae_by_route": {r: float(np.mean(np.abs(y - pers)[(te_a["route"] == r).to_numpy()])) for r in sorted(te_a["route"].unique())},
        "improvement_vs_baseline": float(1 - np.mean(np.abs(y - pers)) / np.mean(np.abs(y - base))),
        "rows": int(len(y))}
    return out


def shap_stability(df: pd.DataFrame, hp: dict, rounds: int, ref_boosters: list[lgb.Booster]) -> dict:
    trm = mask_prior(df[df["split"] == "train"])
    te = df[(df["split"] == "test") & df["ct_prior_ef"].notna()].sample(n=3000, random_state=0, replace=False)
    Xte = te[F.FEATURES]

    def importance(b):
        sv = shap.TreeExplainer(b).shap_values(Xte)
        m = np.abs(sv).mean(axis=0)
        return m, [F.FEATURES[i] for i in np.argsort(-m, kind="stable")]

    ref_m, ref_rank = importance(ref_boosters[1])
    runs = {}
    for s in SHAP_SEEDS:
        b = fit_booster(trm[F.FEATURES], trm[F.TARGET].to_numpy(), 0.5, hp, rounds, s)
        m, rank = importance(b)
        runs[str(s)] = {"rank": rank, "top3": rank[:3], "mean_abs_shap": dict(zip(F.FEATURES, map(float, m)))}
    ranks = [tuple(v["rank"]) for v in runs.values()]
    top3 = [frozenset(v["top3"]) for v in runs.values()]
    return {
        "note": "Mean |SHAP| of the q50 booster on 3,000 held-out test rows that have a prior. 4 features, so the top-3 set is a real choice now.",
        "reference_seed": FINAL_SEED, "reference_rank": ref_rank, "reference_top3": ref_rank[:3],
        "reference_mean_abs_shap": dict(zip(F.FEATURES, map(float, ref_m))),
        "seeds": runs,
        "top3_set_identical_in_all_seeds": bool(len(set(top3)) == 1),
        "top3_set_matches_reference_in": int(sum(t == frozenset(ref_rank[:3]) for t in top3)),
        "full_rank_order_identical_in_all_seeds": bool(len(set(ranks)) == 1),
        "full_rank_order_matches_reference_in": int(sum(tuple(r) == tuple(ref_rank) for r in ranks)),
        "n_seeds": len(SHAP_SEEDS),
    }


ABLATION_VARIANTS = {
    "route + capacity + grid  (round-1 features, no prior)": ["route_code", "capacity_tpy", "grid_gco2_kwh"],
    "route + prior": ["route_code", "ct_prior_ef"],
    "route + grid + prior  (no capacity)": ["route_code", "grid_gco2_kwh", "ct_prior_ef"],
    "route + capacity + grid + prior  (SHIPPED)": F.FEATURES,
}


def _round_cv(r: dict) -> dict:
    return {reg: {k: (round(v, 5) if isinstance(v, float) else v) for k, v in r[reg].items()} for reg in ("with_prior", "no_prior")}


def ablation(tr: pd.DataFrame, hp: dict, rounds: int) -> dict:
    out = {name: _round_cv(grouped_cv(tr, feats, hp, rounds_list=(rounds,))[rounds]) for name, feats in ABLATION_VARIANTS.items()}
    # alternative prior definition, reported but NOT shipped (the choice of the direct emissions factor was made on semantics,
    # before any result: it keeps the indirect term tied to the independent Ember grid feature instead of Climate TRACE's own
    # electricity and grid assumptions): the plant's lagged TOTAL SEE in place of the lagged direct EF.
    alt = tr.assign(ct_prior_ef=tr["hist_see_mean"])
    out["route + capacity + grid + prior = lagged TOTAL SEE  (alternative, NOT shipped)"] = _round_cv(
        grouped_cv(alt, F.FEATURES, hp, rounds_list=(rounds,))[rounds])
    return out


def country_holdout(tr: pd.DataFrame, hp: dict, rounds: int) -> dict:
    """Leave-countries-out CV on the TRAIN plants: how the shipped model behaves in a country it has never seen."""
    r = _round_cv(grouped_cv(tr, F.FEATURES, hp, rounds_list=(rounds,), group_col="iso3")[rounds])
    r["note"] = "5 folds grouped by country; coverage_raw is BEFORE conformal calibration; the prior is the plant's own history, so a new country does not remove it"
    return r


def measure_latency(n: int = 300) -> dict:
    from model.scorer import Scorer
    s = Scorer.load()
    rep = json.loads((F.REPO / "data" / "demo_reports" / "honest_eaf.json").read_text("utf-8"))
    inst = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}
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
    ap.add_argument("--skip-extras", action="store_true", help="skip ablation / SHAP seeds / country hold-out (still writes artefact + hash)")
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
    print(f"rows {len(df)}  plants {df['source_id'].nunique()}  split plants {df.groupby('split')['source_id'].nunique().to_dict()}  "
          f"prior available on {df['ct_prior_ef'].notna().mean():.1%} of rows", flush=True)

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
                selection.append({"hp": hp, "rounds": r, "selection_score": round(m["selection_score"], 5),
                                  "with_prior": {k: round(v, 5) if isinstance(v, float) else v for k, v in m["with_prior"].items()},
                                  "no_prior": {k: round(v, 5) if isinstance(v, float) else v for k, v in m["no_prior"].items()}})
                if best is None or m["selection_score"] < best["score"] - 1e-9:
                    best = {"hp": hp, "rounds": r, "score": m["selection_score"]}
            print("cv", hp, {r: round(m["selection_score"], 5) for r, m in res.items()}, f"{time.time()-t_start:.0f}s", flush=True)
    hp, rounds = best["hp"], best["rounds"]
    print("selected", hp, rounds, flush=True)

    # ---- 4/5. fit, calibrate, freeze (twice: must be byte-identical) ----------------------------------------
    blob1, boosters, artefact = build_artefact(df, hp, rounds)
    blob2, _, _ = build_artefact(df, hp, rounds)
    h1, h2 = hashlib.sha256(blob1).hexdigest(), hashlib.sha256(blob2).hexdigest()
    assert h1 == h2, f"training is not deterministic: {h1} != {h2}"
    ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTEFACT_PATH.write_bytes(blob1)
    version = "0x" + h1
    HASH_PATH.write_bytes(version.encode("ascii"))  # exactly the hash string, no trailing newline
    print(f"artefact {ARTEFACT_PATH.name}  {len(blob1)/1e6:.2f} MB  determinism check: two builds identical", flush=True)

    # ---- 6. evaluate (test plants, once) ----------------------------------------------------------------------
    ev = evaluate(df, boosters, artefact)
    ev_out = {
        "modelVersionHash": version,
        "evaluated_artefact": "trained on the train-split plants only (30 % of them with the prior blanked); the test plants were never used for any decision",
        "honesty_note": ("Ground truth = Climate TRACE v5.10.0 modelled direct+indirect emissions factor. The prior = the same plant's own Climate TRACE history. "
                         "'with_prior' metrics therefore measure consistency with Climate TRACE's own history (persistence), not real-world accuracy; synthetic "
                         "underreports are relative to that history. 'without_prior' shows the new-plant case."),
        "data": {"fixture_sha256": artefact["training_data"]["fixture_sha256"], "rows_by_split": artefact["training_data"]["rows_by_split"],
                 "plants_by_split": artefact["training_data"]["plants_by_split"], "split_seed": SPLIT_SEED},
        "dataset": {
            "rows": int(len(df)), "plants": int(df["source_id"].nunique()), "countries": int(df["iso3"].nunique()),
            "months": [int(df["year"].min()), int(df["year"].max())],
            "plants_by_route_and_split": {f"{a}/{b}": int(n) for (a, b), n in df.groupby(["route", "split"])["source_id"].nunique().items()},
            "rows_with_ember_grid_missing": int(df["grid_gco2_kwh"].isna().sum()),
            "rows_with_prior_by_year": {int(y): round(float(v), 4) for y, v in df.groupby("year")["ct_prior_ef"].apply(lambda s: s.notna().mean()).items()},
        },
        "prior_definition": artefact["features"][3],
        "training_ranges_train_plants": artefact["training_ranges"],
        "hyperparameters": artefact["hyperparameters"],
        "calibration_offsets": artefact["post_processing"]["offsets"],
        "cv_selection_train_plants_only": selection,
        "test": ev,
    }
    if not args.skip_extras:
        ev_out["feature_ablation_grouped_cv_train_plants"] = ablation(tr, hp, rounds)
        ev_out["leave_country_out_cv_train_plants"] = country_holdout(tr, hp, rounds)
        ev_out["shap_stability"] = shap_stability(df, hp, rounds, boosters)
    write_eval(ev_out)
    for k in ("with_prior", "without_prior_same_rows", "without_prior_all_test_rows"):
        p = ev[k]["point_accuracy_and_coverage"]
        d = ev[k]["detection_synthetic"]
        print(f"{k:30s} rows {p['n_rows']:6d}  MAE {p['mae_q50']:.4f} vs {p['mae_route_median_baseline']:.4f} ({p['improvement_vs_baseline']:+.1%})  "
              f"cov {p['coverage_90']:.3f}  width {p['mean_interval_width']:.3f}  red@0.5/0.7/0.85/1.0 "
              f"{d['0.5x']['red_rate_full_rule']:.3f}/{d['0.7x']['red_rate_full_rule']:.3f}/{d['0.85x']['red_rate_full_rule']:.3f}/{d['1x']['red_rate_full_rule']:.3f}", flush=True)
    print("persistence reference", ev["persistence_reference"])

    try:
        ev_out["latency"] = measure_latency()
        write_eval(ev_out)
        print("latency", ev_out["latency"])
    except (ImportError, FileNotFoundError, KeyError, ValueError) as e:  # scorer not yet updated / demo files missing
        print("latency skipped:", repr(e))
    print(f"\nmodelVersionHash {version}\n({time.time()-t_start:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
