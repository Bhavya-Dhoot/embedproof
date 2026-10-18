"""attestor/schemas/report.schema.json (draft 2020-12) and its agreement with validate_report (round 3 item 6)."""
import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from attestor.report import ReportError, validate_report  # noqa: E402

from .conftest import HONEST_REPORT, report  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "attestor" / "schemas" / "report.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
EAF = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}
DEMO_REPORTS = sorted((REPO / "data" / "demo_reports").glob("*.json"))
INSTALLATIONS = {"TR-EAF-0001": EAF, "TR-BOF-0002": {"id": "TR-BOF-0002", "route": "BF-BOF", "capacityTpy": 3500000, "country": "TR"}}


def schema_ok(rep) -> bool:
    return VALIDATOR.is_valid(rep)


def attestor_ok(rep) -> bool:
    try:
        validate_report(rep, EAF)
        return True
    except ReportError:
        return False


def test_schema_is_a_valid_draft_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])
    assert SCHEMA["additionalProperties"] is False


def test_there_are_demo_reports():
    assert [p.name for p in DEMO_REPORTS] == ["amber_eaf.json", "honest_eaf.json", "implausible_bof.json"]


@pytest.mark.parametrize("path", DEMO_REPORTS, ids=lambda p: p.name)
def test_every_demo_report_validates_against_the_schema_and_the_attestor(path):
    rep = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(VALIDATOR.iter_errors(rep), key=str)
    assert errors == []
    validate_report(rep, INSTALLATIONS[rep["installationId"]])


def drop(field):
    r = copy.deepcopy(HONEST_REPORT)
    del r[field]
    return r


INVALID = {
    "missing field": drop("cnCode"),
    "missing schema": drop("schema"),
    "extra field": report(note="x"),
    "wrong schema const": report(schema="embedproof.report.v2"),
    "period 13th month": report(period="2026-13"),
    "period zero month": report(period="2026-00"),
    "period single digit": report(period="2026-9"),
    "period as number": report(period=202609),
    "period before 2000": report(period="1999-12"),
    "period after 2100": report(period="2101-01"),
    "unknown route": report(route="BOF"),
    "cn code too short": report(cnCode="123"),
    "cn code too long": report(cnCode="123456789"),
    "cn code not digits": report(cnCode="7208390a"),
    "cn code as number": report(cnCode=72083900),
    "installation id too long": report(installationId="x" * 33),
    "installation id with space": report(installationId="TR EAF"),
    "production zero": report(productionTonnes=0),
    "production below one tonne": report(productionTonnes=0.5),
    "production as string": report(productionTonnes="5000"),
    "production as bool": report(productionTonnes=True),
    "negative direct emissions": report(directEmissionsTco2=-1.0),
    "negative electricity": report(electricityMwh=-1.0),
    "negative grid factor": report(gridFactorTco2PerMwh=-0.1),
    "401-digit integer": report(productionTonnes=10 ** 400),
    "beyond 2^53": report(electricityMwh=2 ** 53),
    "see too large for uint32": report(seeTco2PerT=429496.7296),
    "null value": report(seeTco2PerT=None),
    "not an object": [HONEST_REPORT],
}


@pytest.mark.parametrize("label", sorted(INVALID))
def test_schema_and_validate_report_both_reject(label):
    rep = INVALID[label]
    assert schema_ok(rep) is False, "schema accepted it"
    assert attestor_ok(rep) is False, "validate_report accepted it"


def test_schema_and_validate_report_both_accept_the_honest_report_and_boundaries():
    for rep in (HONEST_REPORT, report(period="2000-01"), report(period="2100-12"), report(productionTonnes=1,
                directEmissionsTco2=0.0, electricityMwh=0.0, indirectEmissionsTco2=0.0, seeTco2PerT=0.0)):
        if rep["period"] == "2100-12":  # far future: only the cross-field "not later than now" rule rejects it
            assert schema_ok(rep) and not attestor_ok(rep)
        else:
            assert schema_ok(rep) and attestor_ok(rep), rep["period"]


@pytest.mark.parametrize("label,rep", [
    ("indirect not electricity x grid", report(indirectEmissionsTco2=900.0)),
    ("see not (direct + indirect) / production", report(seeTco2PerT=0.2)),
    ("production above nameplate / 12 x 1.2", report(productionTonnes=40_000, directEmissionsTco2=9200.0,
                                                    electricityMwh=18000.0, indirectEmissionsTco2=7560.0)),
    ("period in the future", report(period="2099-01")),
    ("cement route against a steel installation", report(route="CEMENT-DRY")),
])
def test_cross_field_rules_are_enforced_by_the_attestor_only(label, rep):
    """JSON Schema cannot see the registry, the clock or arithmetic between fields: the schema accepts these, validate_report
    rejects them. The schema description says so."""
    assert schema_ok(rep) is True
    assert attestor_ok(rep) is False
