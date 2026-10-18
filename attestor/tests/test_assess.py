"""hasHistory / flags / inputs (round 3 item 2) and the nameplate capacity check (item 1)."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from attestor.app import create_app
from attestor.assess import (CT_ELECTRICITY_MWH_PER_T, ELECTRICITY_BAND, FLAG_ELECTRICITY, FLAG_GRID, Assessor)
from attestor.report import ReportError, validate_report

from .conftest import ARTEFACT, HONEST_REPORT, REPO, export_sign, report

DEMO_DIR = REPO / "data" / "demo_reports"
EAF = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}
BOF = {"id": "TR-BOF-0002", "route": "BF-BOF", "capacityTpy": 3500000, "country": "TR"}
INSTALLATION = {"TR-EAF-0001": EAF, "TR-BOF-0002": BOF}
SCORE = {"riskScore": 4, "band": "green"}


def demo_report(name):
    return json.loads((DEMO_DIR / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ constants and provenance


def test_route_constants_are_climate_trace_other3_values():
    assert CT_ELECTRICITY_MWH_PER_T == {"EAF": 0.87, "BF-BOF": 0.83, "DRI-EAF": 0.87}
    assert ELECTRICITY_BAND == (0.5, 2.0)


# ------------------------------------------------------------------ demo reports (actual files)


@pytest.mark.parametrize("name,expected_mwh_per_t", [("honest_eaf.json", 0.62), ("amber_eaf.json", 0.45),
                                                    ("implausible_bof.json", 0.42)])
def test_demo_reports_raise_no_flags(assessor_real, name, expected_mwh_per_t):
    """Checked against the files in data/demo_reports: 0.62 / 0.45 EAF and 0.42 BF-BOF MWh/t all sit inside
    0.5x-2x of Climate TRACE's 0.87 / 0.83, and the declared grid factor 0.476 is not below 0.8 x Ember's 0.47573."""
    rep = demo_report(name)
    assert rep["electricityMwh"] / rep["productionTonnes"] == pytest.approx(expected_mwh_per_t)
    out = assessor_real.enrich(rep, INSTALLATION[rep["installationId"]], dict(SCORE))
    assert out["flags"] == [] and out["hasHistory"] is True


def test_demo_installations_resolve_their_climate_trace_priors(assessor_real):
    a = assessor_real.resolve(demo_report("honest_eaf.json"), EAF)
    assert a["features"] == {"route": "EAF", "capacityTpy": 300000, "gridGco2PerKwh": 475.73, "ctLaggedPriorEf": 0.08}
    assert a["prior"]["sourceAssetId"] == 1567079 and "asset 1567079" in a["prior"]["source"]
    assert a["grid"]["observationYear"] == 2025 and a["grid"]["country"] == "TUR"
    assert a["artefactSha256"].startswith("0x") and len(a["artefactSha256"]) == 66
    assert a["dataVersion"]["source"].startswith("Climate TRACE v5.10.0")
    b = assessor_real.resolve(demo_report("implausible_bof.json"), BOF)
    assert b["features"]["ctLaggedPriorEf"] == 1.9 and b["prior"]["sourceAssetId"] == 1567073


def test_artefact_hash_is_the_model_version_hash(assessor_real):
    import hashlib

    assert assessor_real.artefact_sha256 == "0x" + hashlib.sha256(ARTEFACT.read_bytes()).hexdigest()


# ------------------------------------------------------------------ no-history branch


def test_plant_without_an_installations_entry_is_scored_without_history(assessor_real):
    new_plant = {"id": "TR-EAF-0777", "route": "EAF", "capacityTpy": 250000, "country": "TR"}
    out = assessor_real.enrich({**HONEST_REPORT, "installationId": "TR-EAF-0777"}, new_plant, dict(SCORE))
    assert out["hasHistory"] is False
    f = out["inputs"]
    assert f["features"]["ctLaggedPriorEf"] is None and f["prior"] == {"value": None, "source": None, "sourceAssetId": None}
    assert f["features"]["gridGco2PerKwh"] == 475.73 and f["grid"]["observationYear"] == 2025  # Ember LOCF for 2026


def test_entry_without_a_prior_is_also_no_history(tmp_path):
    f = tmp_path / "inst.json"
    f.write_text(json.dumps({"TR-EAF-0001": {"country": "TR", "priors": {"capacityTpy": 300000}}}))
    a = Assessor(ARTEFACT, f)
    assert a.enrich(HONEST_REPORT, EAF, dict(SCORE))["hasHistory"] is False


def test_installations_file_is_configurable_and_may_be_missing(tmp_path):
    custom = tmp_path / "mine.json"
    custom.write_text(json.dumps({"TR-EAF-0001": {"country": "TR", "priors": {"ctLaggedPriorEf": 0.5, "gridGco2PerKwh": 400.0}}}))
    out = Assessor(ARTEFACT, custom).enrich(HONEST_REPORT, EAF, dict(SCORE))
    assert out["hasHistory"] is True and out["inputs"]["features"]["ctLaggedPriorEf"] == 0.5
    assert out["inputs"]["grid"]["value"] == 400.0 and out["inputs"]["grid"]["observationYear"] is None
    assert Assessor(ARTEFACT, tmp_path / "does-not-exist.json").enrich(HONEST_REPORT, EAF, dict(SCORE))["hasHistory"] is False


# ------------------------------------------------------------------ GRID_FACTOR_BELOW_EMBER


def test_grid_factor_flag_message_and_threshold(assessor_real):
    low = report(gridFactorTco2PerMwh=0.20, indirectEmissionsTco2=450.0, seeTco2PerT=0.32)
    flags = assessor_real.enrich(low, EAF, dict(SCORE))["flags"]
    assert flags == [{"code": FLAG_GRID, "message": "Declared grid factor 0.20 tCO2/MWh is below Ember's 0.476 for "
                                                    "Türkiye 2025; indirect emissions may be understated."}]
    # threshold is 0.8 x 0.47573 = 0.380584
    assert [f["code"] for f in assessor_real.enrich(report(gridFactorTco2PerMwh=0.3805), EAF, {})["flags"]] == [FLAG_GRID]
    assert assessor_real.enrich(report(gridFactorTco2PerMwh=0.3806), EAF, {})["flags"] == []
    assert assessor_real.enrich(report(gridFactorTco2PerMwh=0.60), EAF, {})["flags"] == []  # above Ember: no flag


# ------------------------------------------------------------------ ELECTRICITY_OUTSIDE_ROUTE_NORM


@pytest.mark.parametrize("mwh,flagged", [(2175.0, False),   # exactly 0.5 x 0.87 = 0.435 MWh/t: inside
                                         (2174.0, True), (8700.0, False),  # exactly 2 x 0.87 = 1.74 MWh/t: inside
                                         (8705.0, True), (3100.0, False)])
def test_electricity_band_boundaries_for_eaf(assessor_real, mwh, flagged):
    flags = assessor_real.enrich(report(electricityMwh=mwh), EAF, {})["flags"]
    assert [f["code"] for f in flags if f["code"] == FLAG_ELECTRICITY] == ([FLAG_ELECTRICITY] if flagged else [])


def test_electricity_flag_message_and_per_route_constant(assessor_real):
    f = assessor_real.enrich(report(electricityMwh=2100.0), EAF, dict(SCORE))["flags"]
    assert f == [{"code": FLAG_ELECTRICITY, "message": "Declared electricity use 0.42 MWh/t is outside 0.5×–2× of "
                                                       "Climate TRACE's modelling assumption (0.87 MWh/t for EAF); coarse "
                                                       "sanity band, not a regulatory norm."}]
    bof = {**demo_report("implausible_bof.json"), "electricityMwh": 100_000.0}  # 0.40 MWh/t < 0.5 x 0.83 = 0.415
    flags = assessor_real.enrich(bof, BOF, {})["flags"]
    assert [x["code"] for x in flags] == [FLAG_ELECTRICITY] and "0.83 MWh/t for BF-BOF" in flags[0]["message"]


def test_flags_are_informational_and_never_touch_the_score(assessor_real):
    score = {"riskScore": 4, "band": "green", "q05": 0.4, "q50": 0.5, "q95": 0.6, "shap": [], "explanation": "x"}
    out = assessor_real.enrich(report(gridFactorTco2PerMwh=0.1, electricityMwh=100.0), EAF, copy.deepcopy(score))
    assert len(out["flags"]) == 2
    assert {k: out[k] for k in score} == score


# ------------------------------------------------------------------ in the service and the API


@pytest.fixture
def client(attestor, cfg):
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        yield c


def test_score_and_attest_carry_the_new_assessment_fields(client, attestor):
    s = client.post("/score", json=HONEST_REPORT).json()
    assert s["hasHistory"] is True and s["flags"] == [] and s["inputs"]["features"]["ctLaggedPriorEf"] == 0.08
    r = client.post("/attest", json={"report": HONEST_REPORT, "signature": export_sign(HONEST_REPORT)}).json()
    assert r["score"]["inputs"] == s["inputs"]
    doc = json.loads(attestor.store.get_document(r["cid"]))
    assert doc["assessment"]["hasHistory"] is True and doc["assessment"]["flags"] == []
    assert doc["assessment"]["inputs"]["artefactSha256"] == attestor.assessor.artefact_sha256
    assert "latencyMs" not in doc["assessment"]


def test_flags_reach_the_signed_document(client, attestor):
    rep = report(electricityMwh=2100.0, indirectEmissionsTco2=882.0, seeTco2PerT=0.4064, period="2026-08")
    r = client.post("/attest", json={"report": rep, "signature": export_sign(rep)}).json()
    doc = json.loads(attestor.store.get_document(r["cid"]))
    assert [f["code"] for f in doc["assessment"]["flags"]] == [FLAG_ELECTRICITY]
    assert r["score"]["flags"] == doc["assessment"]["flags"]


# ------------------------------------------------------------------ capacity check (nameplate / 12 x 1.2)


def scaled(production):
    k = production / 5000.0
    return report(productionTonnes=production, directEmissionsTco2=1150.0 * k, electricityMwh=2250.0 * k,
                  indirectEmissionsTco2=945.0 * k)


@pytest.mark.parametrize("production,ok", [(9999.9, True), (10000, True), (10000.0001, False), (10001, False),
                                            (5000, True)])
def test_production_limit_is_nameplate_over_twelve_times_one_point_two(production, ok):
    inst = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 100000}  # limit 100000 / 12 x 1.2 = 10,000 t
    if ok:
        validate_report(scaled(production), inst)
    else:
        with pytest.raises(ReportError) as e:
            validate_report(scaled(production), inst)
        assert "plausible monthly output" in str(e.value) and "100,000 t/yr / 12 x 1.2 = 10,000.0 t" in str(e.value)


def test_limit_uses_the_on_chain_capacity_not_the_report(client):
    assert client.post("/score", json=scaled(30000)).status_code == 200  # EAF on-chain 300,000 -> limit 30,000 t
    r = client.post("/score", json=scaled(30001))
    assert r.status_code == 422 and "300,000 t/yr / 12 x 1.2 = 30,000.0 t" in r.json()["detail"]
    rep = scaled(30001)
    r = client.post("/attest", json={"report": rep, "signature": export_sign(rep)})
    assert r.status_code == 422 and "plausible monthly output" in r.json()["detail"]


def test_demo_reports_pass_the_capacity_check():
    for name in ("honest_eaf.json", "amber_eaf.json", "implausible_bof.json"):
        rep = demo_report(name)
        validate_report(rep, INSTALLATION[rep["installationId"]])
    # the red demo is plausible in volume (250,000 t <= 3.5 Mt / 10 = 350,000 t); only its SEE is implausible
    assert demo_report("implausible_bof.json")["productionTonnes"] <= BOF["capacityTpy"] / 10
