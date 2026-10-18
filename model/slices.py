"""Slices of the held-out test split, scored with the FROZEN artefact (no refit, artefact never written).

Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe -m model.slices   -> model/artefacts/eval_slices.json

Predictions are exactly those behind model/artefacts/eval.json: same fixtures, same plant split (model.train.make_split), same boosters
loaded from embedproof_model_v1.json, same sort + per-route/per-regime conformal offsets stored in that file, same 4-decimal rounding.
Only the subset of test plants differs. The output file does not feed modelVersionHash (the hash is sha256 of the artefact file alone).

Slice sizes can be tiny; every block carries n and an explicit "not statistically powered" flag. Plant-months of one plant are
strongly correlated, so the effective sample size is the number of plants.
"""
from __future__ import annotations

import hashlib
import json
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

from model import features as F
from model import train as T

SLICES_PATH = T.ARTEFACT_DIR / "eval_slices.json"
POWER_RULE_OF_THUMB_PLANTS = 30  # flag only; a rule of thumb, not a derived threshold


def load_frozen() -> tuple[str, dict, list[lgb.Booster]]:
    blob = T.ARTEFACT_PATH.read_bytes()
    a = json.loads(blob.decode("utf-8"))
    boosters = [lgb.Booster(model_str=a["boosters"][k]) for k in ("q05", "q50", "q95")]
    return "0x" + hashlib.sha256(blob).hexdigest(), a, boosters


def _block(sub: pd.DataFrame, boosters: list[lgb.Booster], offsets: dict, base_med: dict, cbam: dict, with_persistence: bool) -> dict:
    sub = sub.reset_index(drop=True)
    n_plants = int(sub["source_id"].nunique())
    out: dict = {"n_plants": n_plants, "n_plant_months": int(len(sub)),
                 "plants_by_route": {r: int(n) for r, n in sub.groupby("route")["source_id"].nunique().items()}}
    out["statistically_powered"] = bool(n_plants >= POWER_RULE_OF_THUMB_PLANTS)
    if not out["statistically_powered"]:
        out["caveat"] = (f"not statistically powered: n = {n_plants} plants ({len(sub)} plant-months, strongly correlated within plant); "
                         "treat every number as an anecdote about these plants, not an estimate for Turkiye")
    if len(sub) == 0:
        return out
    y = sub[F.TARGET].to_numpy()
    routes = sub["route"].to_numpy()
    P = np.round(T.apply_calibration(T.predict_sorted(boosters, sub[F.FEATURES]), routes, offsets), 4)
    q05, q50, q95 = P[:, 0], P[:, 1], P[:, 2]
    base = sub["route"].map(base_med).to_numpy()
    ae_m, ae_b = np.abs(y - q50), np.abs(y - base)
    out["mae_q50"] = float(ae_m.mean())
    out["mae_route_median_baseline"] = float(ae_b.mean())
    out["improvement_vs_route_median"] = float(1 - ae_m.mean() / ae_b.mean())
    if with_persistence:
        ae_p = np.abs(y - sub["hist_see_mean"].to_numpy())
        out["mae_persistence_lagged_total_see"] = float(ae_p.mean())
        out["q50_mae_relative_to_persistence"] = float(ae_m.mean() / ae_p.mean())  # > 1 means persistence is more accurate
    cov = (y >= q05) & (y <= q95)
    out["coverage_90"] = float(cov.mean())
    out["mean_interval_width"] = float((q95 - q05).mean())

    dflt = np.array([(cbam.get(i) or cbam["OTHER"]).get(r, cbam["OTHER"][r])["total"] for i, r in zip(sub["iso3"], routes)])
    det = {}
    for f in (*T.DETECTION_FACTORS, 1.0):
        decl = np.round(f * y, 6)
        bh = (decl < T.CBAM_HALF * dflt).astype(float)
        red = T.risk_vec(q05, q50, decl, bh) > T.RED
        red_i = T.risk_vec(q05, q50, decl, np.zeros(len(y))) > T.RED
        det[f"{f:g}x"] = {"synthetic": f != 1.0, "red_rate_full_rule": float(red.mean()), "red_rate_interval_only": float(red_i.mean())}
    det["note"] = "red_rate at factor < 1 is recall on SYNTHETIC underreports (factor x Climate TRACE value); at 1x it is the false-red rate"
    out["detection_synthetic"] = det

    rows = []
    for sid, g in sub.groupby("source_id", sort=True):
        m = (sub["source_id"] == sid).to_numpy()
        rows.append({"climateTraceSourceId": int(sid), "route": g["route"].iloc[0], "plant_months": int(m.sum()),
                     "mae_q50": float(ae_m[m].mean()), "coverage_90": float(cov[m].mean()),
                     "mean_q05_q50_q95": [float(q05[m].mean()), float(q50[m].mean()), float(q95[m].mean())],
                     "mean_truth": float(y[m].mean())})
    out["per_plant"] = rows
    return out


def slice_report(df: pd.DataFrame, a: dict, boosters: list[lgb.Booster], mask: pd.Series) -> dict:
    """Metrics for the test plants selected by `mask` (a boolean Series aligned with df)."""
    tr = df[df["split"] == "train"]
    base_med = tr.groupby("route")[F.TARGET].median().to_dict()
    cbam, _ = T.cbam_tables()
    offs = a["post_processing"]["offsets"]
    te = df[(df["split"] == "test") & mask]
    has = te["ct_prior_ef"].notna()
    te_a = te[has]
    return {
        "with_prior": _block(te_a, boosters, offs["with_prior"], base_med, cbam, with_persistence=True),
        "without_prior_same_rows": _block(te_a.assign(ct_prior_ef=np.nan), boosters, offs["no_prior"], base_med, cbam, with_persistence=False),
        "without_prior_all_rows": _block(te.assign(ct_prior_ef=np.nan), boosters, offs["no_prior"], base_med, cbam, with_persistence=False),
        "rows_without_history_naturally": int((~has).sum()),
    }


def compute() -> dict:
    version, a, boosters = load_frozen()
    df = T.make_split(F.load_training_frame())
    return {
        "modelVersionHash": version,
        "how": ("Frozen artefact, no refit. Same test plants, fixtures, calibration offsets and rounding as eval.json; only the subset differs. "
                "Truth = Climate TRACE v5.10.0 modelled SEE and the prior is the plant's own Climate TRACE history, so these numbers measure "
                "persistence/consistency with Climate TRACE, not real-world accuracy (see MODEL_CARD.md section 0)."),
        "feeds_model_version_hash": False,
        "turkiye": {
            "definition": "iso3 == TUR among the held-out TEST plants of the round-2 split (seed %d)" % T.SPLIT_SEED,
            **slice_report(df, a, boosters, df["iso3"] == "TUR"),
        },
        "all_test_plants_reference": {"definition": "every held-out test plant (the population behind eval.json)",
                                      **slice_report(df, a, boosters, pd.Series(True, index=df.index))},
    }


def main() -> None:
    before = hashlib.sha256(T.ARTEFACT_PATH.read_bytes()).hexdigest()
    rep = compute()
    after = hashlib.sha256(T.ARTEFACT_PATH.read_bytes()).hexdigest()
    assert before == after, "artefact changed while computing slices"
    with open(SLICES_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rep, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(rep["turkiye"], indent=1))
    print("artefact unchanged:", "0x" + after)


if __name__ == "__main__":
    sys.exit(main())
