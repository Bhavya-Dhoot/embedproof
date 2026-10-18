"""Tests for the EmbedProof scorer. Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe -m pytest model/tests -q"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from model import features as F
from model import scorer as S
from model import slices as SL
from model import train as T

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "data" / "demo_reports"
RESULT_KEYS = {"riskScore", "band", "q05", "q50", "q95", "expectedLow", "expectedHigh", "seeDeclared", "cbamDefault",
               "belowHalfDefault", "modelVersionHash", "shap", "explanation", "latencyMs"}
INST_EAF = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}
INST_BOF = {"id": "TR-BOF-0002", "route": "BF_BOF", "capacityTpy": 3500000, "country": "TR"}


@pytest.fixture(scope="module")
def scorer() -> S.Scorer:
    return S.Scorer.load()


def load_report(name: str) -> dict:
    return json.loads((REPORTS / name).read_text("utf-8"))


# ------------------------------------------------------------------ risk-score formula (INTERFACES section 7)
@pytest.mark.parametrize("see, below, expected", [
    (1.00, False, 0),      # at q05
    (1.20, False, 0),      # between q05 and q50
    (1.80, False, 0),      # at / above q50
    (3.00, False, 0),      # far above the interval
    (0.75, False, 50),     # half-way between q05 and q05 - (q50 - q05)
    (0.50, False, 100),    # exactly one interval-width below q05
    (0.10, False, 100),    # far below: clipped at 100
    (1.20, True, 20),      # +20 rule alone
    (0.75, True, 70),      # 50 + 20
    (0.50, True, 100),     # 100 + 20 clipped to 100
])
def test_risk_formula_cases(see, below, expected):
    # q05 = 1.0, q50 = 1.5 -> width 0.5 (binary-exact numbers)
    assert S.risk_score(1.0, 1.5, see, below) == expected


def test_risk_rounds_half_up():
    assert S.risk_score(1.0, 1.5, 0.9375, False) == 13   # 12.5 -> 13 (not banker's 12)
    assert S.risk_score(1.0, 1.5, 0.9375, True) == 33    # 32.5 -> 33


def test_risk_degenerate_interval():
    assert S.risk_score(1.0, 1.0, 0.9, False) == 100
    assert S.risk_score(1.0, 1.0, 1.1, False) == 0
    assert S.risk_score(1.0, 0.9, 0.5, False) == 100  # crossed quantiles must not crash


@pytest.mark.parametrize("score, band", [(0, "green"), (30, "green"), (31, "amber"), (60, "amber"), (61, "red"), (100, "red")])
def test_band_thresholds(score, band):
    assert S.band_for(score) == band


def test_vectorised_risk_matches_scalar():
    rng = np.random.RandomState(0)
    q05 = np.round(rng.uniform(0.2, 2.5, 500), 4)
    q50 = np.round(q05 + rng.uniform(0.0, 0.6, 500), 4)
    see = np.round(rng.uniform(0.05, 3.0, 500), 4)
    bh = rng.rand(500) < 0.4
    vec = T.risk_vec(q05, q50, see, bh.astype(float))
    ref = np.array([S.risk_score(a, b, c, d) for a, b, c, d in zip(q05, q50, see, bh)])
    assert (vec == ref).all()


# ------------------------------------------------------------------ demo reports
def test_demo_reports_are_internally_consistent():
    for p in sorted(REPORTS.glob("*.json")):
        r = json.loads(p.read_text("utf-8"))
        assert r["schema"] == "embedproof.report.v1" and r["productionTonnes"] > 0
        assert r["indirectEmissionsTco2"] == pytest.approx(r["electricityMwh"] * r["gridFactorTco2PerMwh"], rel=0.01)
        assert r["seeTco2PerT"] == pytest.approx((r["directEmissionsTco2"] + r["indirectEmissionsTco2"]) / r["productionTonnes"], rel=0.01)


def test_honest_eaf_is_green(scorer):
    res = scorer.score_report(load_report("honest_eaf.json"), INST_EAF)
    assert res["band"] == "green" and res["riskScore"] <= 30


def test_implausible_bof_is_red(scorer):
    res = scorer.score_report(load_report("implausible_bof.json"), INST_BOF)
    assert res["band"] == "red" and res["riskScore"] > 60


def test_amber_eaf_is_amber(scorer):
    res = scorer.score_report(load_report("amber_eaf.json"), INST_EAF)
    assert res["band"] == "amber" and 31 <= res["riskScore"] <= 60


# ------------------------------------------------------------------ ScoreResult contract (INTERFACES section 6)
def test_result_has_exact_keys_and_is_json_serialisable(scorer):
    res = scorer.score_report(load_report("honest_eaf.json"), INST_EAF)
    assert set(res) == RESULT_KEYS
    json.dumps(res)
    assert isinstance(res["riskScore"], int) and res["band"] in {"green", "amber", "red"}
    assert res["modelVersionHash"] == scorer.model_version_hash
    assert res["expectedLow"] == res["q05"] and res["expectedHigh"] == res["q95"]
    assert res["q05"] <= res["q50"] <= res["q95"]
    assert len(res["shap"]) == 3
    assert [abs(s["contribution"]) for s in res["shap"]] == sorted((abs(s["contribution"]) for s in res["shap"]), reverse=True)
    for s in res["shap"]:
        assert set(s) == {"feature", "label", "value", "contribution"}
    assert res["explanation"].endswith(".") and "\n" not in res["explanation"]


def test_published_numbers_reproduce_the_risk_score(scorer):
    for name, inst in (("honest_eaf.json", INST_EAF), ("implausible_bof.json", INST_BOF), ("amber_eaf.json", INST_EAF)):
        res = scorer.score_report(load_report(name), inst)
        again = S.risk_score(res["q05"], res["q50"], res["seeDeclared"], res["belowHalfDefault"])
        assert again == res["riskScore"]
        assert res["belowHalfDefault"] == (res["seeDeclared"] < 0.5 * res["cbamDefault"])


def test_shap_values_add_up_to_the_q50_prediction(scorer):
    res = scorer.score_report(load_report("implausible_bof.json"), INST_BOF)
    x = F.feature_row("BF-BOF", 3500000, 475.73, 1.9)
    base = float(np.ravel(scorer._explainer.expected_value)[0])
    sv = np.asarray(scorer._explainer.shap_values(x, check_additivity=False))[0]
    assert base + sv.sum() == pytest.approx(float(scorer._boosters["q50"].predict(x)[0]), abs=1e-6)
    assert res["shap"][0]["contribution"] == pytest.approx(float(sorted(sv, key=abs, reverse=True)[0]), abs=1e-4)


def test_quantiles_never_cross(scorer):
    for route in ("BF-BOF", "EAF", "DRI-EAF"):
        for cap in (None, 50_000, 1_000_000, 20_000_000):
            for grid in (None, 5.0, 475.0, 900.0):
                for prior in (None, 0.05, 0.4, 1.9, 2.5):
                    rep = {"period": "2026-09", "seeTco2PerT": 0.5, "route": route}
                    inst = {"id": "XX", "route": route, "capacityTpy": cap, "country": "ZZ", "ctLaggedPriorEf": prior}
                    if grid is not None:
                        scorer._demo["XX"] = {"priors": {"gridGco2PerKwh": grid}}
                    else:
                        scorer._demo.pop("XX", None)
                    res = scorer.score_report(rep, inst)
                    assert 0 <= res["q05"] <= res["q50"] <= res["q95"]
    scorer._demo.pop("XX", None)


# ------------------------------------------------------------------ unknown installations / unsupported routes
def test_unknown_installation_scores_with_missing_priors(scorer):
    rep = {"schema": "embedproof.report.v1", "installationId": "XX-9999", "period": "2026-09", "route": "EAF", "productionTonnes": 100,
           "seeTco2PerT": 0.5}
    res = scorer.score_report(rep, {"id": "XX-9999", "route": "EAF"})  # no capacity, no country, no prior
    assert set(res) == RESULT_KEYS and res["riskScore"] in range(101)
    assert len(res["shap"]) == 3
    values = {s["feature"]: s["value"] for s in res["shap"]}
    for name in ("capacityTpy", "gridIntensity", "ctPrior"):
        assert values.get(name) is None
    assert "no Climate TRACE history" in res["explanation"]


def test_unknown_country_uses_other_countries_cbam_default(scorer):
    res = scorer.score_report({"period": "2026-09", "seeTco2PerT": 2.0, "route": "BF-BOF"},
                              {"id": "XX-1", "route": "BF_BOF", "capacityTpy": 1_000_000, "country": "ZZ"})
    assert res["cbamDefault"] == pytest.approx(4.049)


def test_turkiye_cbam_default_is_the_ec_table_value(scorer):
    assert scorer.score_report(load_report("honest_eaf.json"), INST_EAF)["cbamDefault"] == pytest.approx(2.31)
    assert scorer.score_report(load_report("implausible_bof.json"), INST_BOF)["cbamDefault"] == pytest.approx(2.428)


@pytest.mark.parametrize("route", ["CEMENT-DRY", "CEMENT-WET", "CEMENT_DRY", "cement", "H2-DRI", ""])
def test_unsupported_route_raises_value_error(scorer, route):
    with pytest.raises(ValueError):
        scorer.score_report({"period": "2026-09", "seeTco2PerT": 0.8, "route": route}, {"id": "TR-CEM-1", "route": route, "capacityTpy": 1_000_000, "country": "TR"})


def test_route_mismatch_and_bad_inputs_raise(scorer):
    with pytest.raises(ValueError):
        scorer.score_report({"period": "2026-09", "seeTco2PerT": 0.5, "route": "EAF"}, INST_BOF)
    for bad in (0, -1, float("nan"), None, "abc"):
        with pytest.raises(ValueError):
            scorer.score_report({"period": "2026-09", "seeTco2PerT": bad, "route": "EAF"}, INST_EAF)
    with pytest.raises(ValueError):
        scorer.score_report({"period": "2026/09", "seeTco2PerT": 0.5, "route": "EAF"}, INST_EAF)


# ------------------------------------------------------------------ artefact hash + determinism
def test_model_version_hash_matches_artefact_bytes(scorer):
    blob = (REPO / "model" / "artefacts" / "embedproof_model_v1.json").read_bytes()
    expected = "0x" + hashlib.sha256(blob).hexdigest()
    assert scorer.model_version_hash == expected
    assert (REPO / "model" / "artefacts" / "model_version.txt").read_text("ascii").strip() == expected
    assert len(expected) == 66


def test_retraining_reproduces_the_identical_artefact(scorer):
    """Re-fit from the committed fixtures with the stored hyper-parameters; bytes (hence hash) must be identical."""
    a = json.loads((REPO / "model" / "artefacts" / "embedproof_model_v1.json").read_text("utf-8"))
    h = a["hyperparameters"]
    df = T.make_split(F.load_training_frame())
    blob, _, _ = T.build_artefact(df, {"num_leaves": h["num_leaves"], "min_data_in_leaf": h["min_data_in_leaf"]}, h["rounds"], h["seed"])
    assert "0x" + hashlib.sha256(blob).hexdigest() == scorer.model_version_hash


def test_artefact_records_training_data_snapshot(scorer):
    a = json.loads((REPO / "model" / "artefacts" / "embedproof_model_v1.json").read_text("utf-8"))
    snap = a["training_data"]["snapshot_sha256"]
    assert snap == F.sha256_file(F.FIXTURES / "steel_facility_months.csv")
    assert set(a["boosters"]) == {"q05", "q50", "q95"} and a["cbam_defaults"]["by_country"]["TUR"]["EAF"]["total"] == pytest.approx(2.31)


# ------------------------------------------------------------------ latency
def test_inference_latency_under_500ms_including_shap(scorer):
    rep = load_report("implausible_bof.json")
    scorer.score_report(rep, INST_BOF)  # warm-up (first SHAP call compiles nothing but touches caches)
    t = []
    for _ in range(50):
        t0 = time.perf_counter()
        res = scorer.score_report(rep, INST_BOF)
        t.append((time.perf_counter() - t0) * 1000)
    assert max(t) < 500, f"max latency {max(t):.1f} ms"
    assert res["latencyMs"] < 500 and math.isfinite(res["latencyMs"])


# ------------------------------------------------------------------ lagged Climate TRACE prior
def _plant_frame(values, start=(2022, 1), sid=1):
    rows = []
    y, m = start
    for v in values:
        rows.append({"source_id": sid, "year": y, "month": m, "ct_ef_direct": v})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return pd.DataFrame(rows)


def test_lagged_prior_window_is_m_minus_14_to_m_minus_3_and_never_looks_ahead():
    vals = [float(i) for i in range(1, 31)]  # month index t = 0..29 holds value t+1
    df = _plant_frame(vals)
    mean, cnt = F.lagged_mean(df, "ct_ef_direct")
    t = 20
    assert mean[t] == pytest.approx(np.mean(vals[t - 14:t - 2]))  # months t-14..t-3 inclusive
    assert cnt[t] == 12
    # changing months m-2, m-1, m and the future must not change the prior of month m
    changed = df.copy()
    changed.loc[changed.index >= t - 2, "ct_ef_direct"] = 999.0
    mean2, _ = F.lagged_mean(changed, "ct_ef_direct")
    assert mean2[t] == mean[t]
    # ...but changing m-3 must
    changed = df.copy()
    changed.loc[t - 3, "ct_ef_direct"] = 999.0
    assert F.lagged_mean(changed, "ct_ef_direct")[0][t] != mean[t]


def test_lagged_prior_is_nan_without_enough_history():
    df = _plant_frame([1.0] * 12)
    mean, cnt = F.lagged_mean(df, "ct_ef_direct")
    assert np.isnan(mean[:8]).all()      # months 0..7: fewer than 6 earlier months inside m-14..m-3
    assert not np.isnan(mean[8:]).any()  # month 8 is the first with 6 (months 0..5)
    assert cnt[0] == 0


def test_training_frame_prior_uses_only_the_plants_own_earlier_months():
    df = F.load_training_frame()
    sample = df[df["ct_prior_ef"].notna()].sample(40, random_state=3)
    t_all = (df["year"] - 2021) * 12 + df["month"] - 1
    for idx, r in sample.iterrows():
        t = (r.year - 2021) * 12 + r.month - 1
        own = df[(df["source_id"] == r.source_id) & (t_all >= t - 14) & (t_all <= t - 3)]
        assert len(own) >= F.PRIOR_MIN_MONTHS
        assert own["ct_ef_direct"].mean() == pytest.approx(r.ct_prior_ef, abs=1e-9)
        assert (own["source_id"] == r.source_id).all() and ((t_all[own.index]) <= t - 3).all()


def test_prior_narrows_the_interval_and_no_prior_uses_the_wider_branch(scorer):
    rep = load_report("honest_eaf.json")
    with_prior = scorer.score_report(rep, INST_EAF)  # known installation -> borrowed prior
    no_prior = scorer.score_report(rep, dict(INST_EAF, id="XX-0001", ctLaggedPriorEf=None))
    assert "lagged prior" in with_prior["explanation"] and "no Climate TRACE history" in no_prior["explanation"]
    assert (with_prior["q95"] - with_prior["q05"]) < (no_prior["q95"] - no_prior["q05"])


def test_explicit_installation_prior_overrides_the_demo_file(scorer):
    rep = load_report("honest_eaf.json")
    a = scorer.score_report(rep, INST_EAF)
    b = scorer.score_report(rep, dict(INST_EAF, ctLaggedPriorEf=0.45))
    assert a["q50"] != b["q50"]


def test_demo_installations_carry_a_borrowed_lagged_prior():
    demo = json.loads((REPO / "data" / "installations_demo.json").read_text("utf-8"))
    for key, ref_id in (("TR-EAF-0001", 1567079), ("TR-BOF-0002", 1567073)):
        assert demo[key]["fictional"] is True and demo[key]["priors"]["ctLaggedPriorEf"] > 0
        assert demo[key]["climateTraceReference"]["climateTraceSourceId"] == ref_id


def test_artefact_has_per_route_offsets_for_both_regimes():
    a = json.loads((REPO / "model" / "artefacts" / "embedproof_model_v1.json").read_text("utf-8"))
    offs = a["post_processing"]["offsets"]
    assert set(offs) == {"with_prior", "no_prior"}
    for regime in offs.values():
        assert set(regime) == {"BF-BOF", "EAF", "DRI-EAF"}
        for o in regime.values():
            assert o["n_calibration_plants"] >= 5 and math.isfinite(o["lower"]) and math.isfinite(o["upper"])
    assert [f["name"] for f in a["features"]] == F.FEATURES


# ------------------------------------------------------------------ held-out slices (frozen artefact, no refit)
def _assert_same(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and list(a) == list(b), path
        for k in a:
            _assert_same(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_same(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12), path
    else:
        assert a == b, path


def test_turkiye_slice_recomputes_deterministically_without_touching_the_artefact(scorer):
    path = REPO / "model" / "artefacts" / "embedproof_model_v1.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    rep = SL.compute()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert rep["modelVersionHash"] == scorer.model_version_hash and rep["feeds_model_version_hash"] is False
    stored = json.loads((REPO / "model" / "artefacts" / "eval_slices.json").read_text("utf-8"))
    _assert_same(rep, stored)


def test_slice_machinery_reproduces_eval_json_on_the_full_test_set():
    rep = SL.compute()
    ev = json.loads((REPO / "model" / "artefacts" / "eval.json").read_text("utf-8"))["test"]
    for key in ("with_prior", "without_prior_same_rows"):
        ref, want = rep["all_test_plants_reference"][key], ev[key]
        p, d = want["point_accuracy_and_coverage"], want["detection_synthetic"]
        assert ref["n_plant_months"] == p["n_rows"] and ref["n_plants"] == p["n_plants"]
        assert ref["mae_q50"] == pytest.approx(p["mae_q50"], rel=1e-9)
        assert ref["coverage_90"] == pytest.approx(p["coverage_90"], rel=1e-9)
        for f in ("0.5x", "0.7x", "0.85x", "1x"):
            assert ref["detection_synthetic"][f]["red_rate_full_rule"] == pytest.approx(d[f]["red_rate_full_rule"], rel=1e-9)


def test_turkiye_slice_is_only_held_out_turkish_plants_and_flagged_underpowered():
    rep = SL.compute()["turkiye"]
    df = T.make_split(F.load_training_frame())
    tur_test = df[(df["iso3"] == "TUR") & (df["split"] == "test")]
    assert rep["with_prior"]["n_plants"] + 0 <= tur_test["source_id"].nunique() == rep["without_prior_all_rows"]["n_plants"]
    assert rep["without_prior_all_rows"]["n_plant_months"] == len(tur_test)
    assert not set(tur_test["source_id"]) & set(df[df["split"] != "test"]["source_id"])
    for key in ("with_prior", "without_prior_same_rows", "without_prior_all_rows"):
        assert rep[key]["statistically_powered"] is False and "not statistically powered" in rep[key]["caveat"]
