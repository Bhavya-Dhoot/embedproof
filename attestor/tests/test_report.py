import math

import pytest

from attestor.report import (ReportError, installation_id_bytes32, period_to_int, see_to_chain, tonnes_to_chain,
                             validate_report)

from .conftest import HONEST_REPORT, report

EAF = {"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 100000, "country": "TR"}


def errors_of(rep, inst=EAF):
    with pytest.raises(ReportError) as e:
        validate_report(rep, inst)
    return " | ".join(e.value.errors)


def test_honest_report_is_valid():
    validate_report(HONEST_REPORT, EAF)


def test_not_an_object_or_wrong_schema():
    assert "JSON object" in errors_of([1, 2])
    assert "schema" in errors_of(report(schema="embedproof.report.v2"))


def test_missing_and_unknown_fields_rejected():
    rep = report()
    del rep["cnCode"]
    assert "missing fields: cnCode" in errors_of(rep)
    assert "unknown fields: note" in errors_of(report(note="x"))


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "2026-9", "202609", "26-09", "2026-09-01", 202609, None, "1999-12",
                                    "2026-09" + chr(10), "".join(chr(c) for c in (0x662, 0x660, 0x662, 0x666)) + "-" + chr(0x660) + chr(0x669)])  # newline, Arabic-Indic digits
def test_bad_period(period):
    assert "period" in errors_of(report(period=period))


def test_route_must_be_known_and_match_registry():
    assert "route must be one of" in errors_of(report(route="BOF"))
    msg = errors_of(report(route="BF-BOF"))
    assert "does not match the registry route 'EAF'" in msg


def test_unknown_installation():
    assert "not registered" in errors_of(HONEST_REPORT, None)


@pytest.mark.parametrize("bad_id", ["", "x" * 33, "TR EAF", "é", 7, None, "TR-EAF-0001" + chr(10)])
def test_bad_installation_id(bad_id):
    assert "installationId" in errors_of(report(installationId=bad_id))


@pytest.mark.parametrize("field", ["productionTonnes", "directEmissionsTco2", "electricityMwh",
                                   "gridFactorTco2PerMwh", "indirectEmissionsTco2", "seeTco2PerT"])
@pytest.mark.parametrize("value", ["5", None, True, math.nan, math.inf])
def test_numeric_fields_must_be_finite_numbers(field, value):
    assert field in errors_of(report(**{field: value}))


@pytest.mark.parametrize("cn", ["72083900" + chr(10), "123", "123456789", "7208390a", 72083900, "".join(chr(c) for c in (0x667, 0x662, 0x660, 0x668))])
def test_bad_cn_code(cn):
    assert "cnCode" in errors_of(report(cnCode=cn))


def test_production_must_be_positive_and_whole_tonne():
    assert "productionTonnes" in errors_of(report(productionTonnes=0))
    assert "productionTonnes" in errors_of(report(productionTonnes=-5))
    assert "productionTonnes" in errors_of(report(productionTonnes=0.5))


def test_negative_values_rejected():
    assert "out of range" in errors_of(report(directEmissionsTco2=-1.0))


def test_indirect_must_match_electricity_times_grid_within_one_percent():
    # expected 945.0
    validate_report(report(indirectEmissionsTco2=945.0 * 1.0099), EAF)
    validate_report(report(indirectEmissionsTco2=945.0 * 0.9901), EAF)
    assert "indirectEmissionsTco2" in errors_of(report(indirectEmissionsTco2=945.0 * 1.0101))
    assert "indirectEmissionsTco2" in errors_of(report(indirectEmissionsTco2=945.0 * 0.989))


def test_see_must_match_total_over_production_within_one_percent():
    # expected 0.419
    validate_report(report(seeTco2PerT=0.419 * 1.0099), EAF)
    assert "seeTco2PerT" in errors_of(report(seeTco2PerT=0.419 * 1.02))
    assert "seeTco2PerT" in errors_of(report(seeTco2PerT=0.2))  # the classic under-report


def test_zero_electricity_requires_zero_indirect():
    ok = report(electricityMwh=0.0, indirectEmissionsTco2=0.0, seeTco2PerT=1150.0 / 5000)
    validate_report(ok, EAF)
    assert "indirectEmissionsTco2" in errors_of({**ok, "indirectEmissionsTco2": 5.0})


def test_all_errors_are_reported_together():
    msg = errors_of(report(route="BOF", cnCode="x", period="bad"))
    assert "route" in msg and "cnCode" in msg and "period" in msg


# ---- on-chain mappings (INTERFACES section 4)


def test_period_mapping():
    assert period_to_int("2026-09") == 202609
    assert period_to_int("2027-12") == 202712


def test_see_rounds_half_up_without_float_artefacts():
    assert see_to_chain(0.419) == 4190
    assert see_to_chain(0.4195) == 4195
    assert see_to_chain(0.41955) == 4196  # exactly half in decimal -> up (binary 0.41955*1e4 would give 4195.499..)
    assert see_to_chain(0.00005) == 1
    assert see_to_chain(1.0) == 10000
    assert see_to_chain(2.12345) == 21235


def test_tonnes_floor():
    assert tonnes_to_chain(5000) == 5000
    assert tonnes_to_chain(5000.9) == 5000


def test_installation_id_bytes32_is_right_padded_ascii():
    b = installation_id_bytes32("TR-EAF-0001")
    assert len(b) == 32 and b[:11] == b"TR-EAF-0001" and b[11:] == bytes(21)
    with pytest.raises(ValueError):
        installation_id_bytes32("x" * 33)
