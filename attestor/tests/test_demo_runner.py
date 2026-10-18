"""Hosted demo runner (round 3 item 5): endpoints, scenario steps, period choice and band preservation."""
import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from attestor.app import create_app
from attestor.config import ANVIL_DEMO_IMPORTERS, ANVIL_DEMO_KEYS, Config, ConfigError, DemoRunnerConfig
from attestor.demo_runner import DemoRunner
from attestor.scenarios import MAX_MONTHS_BACK, clamp_to_current_month, free_period, load_report, months_back

from .conftest import ADDR, KEYS

CERT_RE = re.compile(r"/verify/(\d+)#k=[A-Za-z0-9_-]{43}")


@pytest.fixture
def demo_cfg(cfg):
    return dataclasses.replace(cfg, demo=DemoRunnerConfig(
        exporter_key=KEYS["exporterA"], exporter_b_key=KEYS["exporterB"], verifier_key=KEYS["verifier"],
        importer_a=ADDR["importerA"], importer_b=ADDR["importerB"]), client_ip_header="x-real-ip")


@pytest.fixture
def client(attestor, demo_cfg):
    with TestClient(create_app(attestor=attestor, cfg=demo_cfg)) as c:
        yield c


def run(client, scenario, ip="198.51.100.1"):
    return client.post(f"/demo/run/{scenario}", headers={"x-real-ip": ip})


# ------------------------------------------------------------------ configuration guards


def test_demo_runner_is_off_by_default_and_404s(attestor, cfg):
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        assert c.get("/demo/status").json() == {"enabled": False, "chainId": 31337}
        r = c.post("/demo/run/green")
        assert r.status_code == 404 and "not enabled" in r.json()["detail"]
        assert attestor.chain.batches == {}


def test_status_when_enabled(client):
    assert client.get("/demo/status").json() == {"enabled": True, "chainId": 31337}


def test_unknown_scenario_is_404(client):
    assert run(client, "purple").status_code == 404


@pytest.mark.parametrize("chain_id", [1, 8453, 10, 11155111, 137, 0])
def test_runner_refuses_to_start_on_other_chains(monkeypatch, chain_id):
    monkeypatch.setenv("DEMO_RUNNER", "1")
    monkeypatch.setenv("CHAIN_ID", str(chain_id))
    monkeypatch.setenv("ATTESTOR_PRIVATE_KEY", "0x" + "11" * 32)
    for name in ("DEMO_EXPORTER_KEY", "DEMO_VERIFIER_KEY", "DEMO_IMPORTER_A", "DEMO_IMPORTER_B"):
        monkeypatch.setenv(name, "0x" + "22" * 32 if "KEY" in name else ADDR["importerA"])
    with pytest.raises(ConfigError, match="only allowed on chains"):
        Config.from_env()


def test_defaults_to_anvil_demo_accounts_on_31337_only(monkeypatch):
    for name in ("DEMO_EXPORTER_KEY", "DEMO_VERIFIER_KEY", "DEMO_IMPORTER_A", "DEMO_IMPORTER_B", "DEMO_EXPORTER_B_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEMO_RUNNER", "1")
    monkeypatch.setenv("CHAIN_ID", "31337")
    d = Config.from_env().demo
    assert d.exporter_key == ANVIL_DEMO_KEYS["exporterA"] and d.verifier_key == ANVIL_DEMO_KEYS["verifier"]
    assert d.exporter_b_key == ANVIL_DEMO_KEYS["exporterB"]
    assert (d.importer_a, d.importer_b) == (ANVIL_DEMO_IMPORTERS["importerA"], ANVIL_DEMO_IMPORTERS["importerB"])
    monkeypatch.setenv("CHAIN_ID", "84532")
    monkeypatch.setenv("ATTESTOR_PRIVATE_KEY", "0x" + "11" * 32)
    with pytest.raises(ConfigError, match="DEMO_EXPORTER_KEY"):  # no Anvil defaults on a public testnet
        Config.from_env()
    monkeypatch.setenv("DEMO_EXPORTER_KEY", "22" * 32)
    monkeypatch.setenv("DEMO_VERIFIER_KEY", "0x" + "33" * 32)
    monkeypatch.setenv("DEMO_IMPORTER_A", ADDR["importerA"])
    monkeypatch.setenv("DEMO_IMPORTER_B", ADDR["importerB"])
    d = Config.from_env().demo
    assert d.exporter_key == "0x" + "22" * 32 and d.exporter_b_key is None


def test_demo_disabled_unless_exactly_one(monkeypatch):
    monkeypatch.setenv("CHAIN_ID", "31337")
    for value in ("", "0", "true", "yes", "2"):
        monkeypatch.setenv("DEMO_RUNNER", value)
        assert Config.from_env().demo is None


def test_startup_logs_a_demo_keys_warning(attestor, demo_cfg, caplog):
    with caplog.at_level("WARNING", logger="attestor.app"):
        with TestClient(create_app(attestor=attestor, cfg=demo_cfg)):
            pass
    assert any("DEMO RUNNER ENABLED" in r.message and "DEMO-ONLY keys" in r.message for r in caplog.records)


# ------------------------------------------------------------------ scenario results


def assert_shape(body, scenario):
    assert set(body) == {"scenario", "chainId", "batchId", "certificatePath", "steps"}
    assert body["scenario"] == scenario and body["chainId"] == 31337
    for s in body["steps"]:
        assert set(s) == {"label", "ok", "expected", "txHash", "detail"}
        assert isinstance(s["label"], str) and isinstance(s["ok"], bool) and isinstance(s["expected"], bool)
        assert s["txHash"] is None or re.fullmatch(r"0x[0-9a-f]{64}", s["txHash"])
        assert isinstance(s["detail"], str)


def test_green(client, attestor):
    r = run(client, "green")
    assert r.status_code == 200, r.text
    body = r.json()
    assert_shape(body, "green")
    assert all(s["ok"] and s["expected"] for s in body["steps"]) and len(body["steps"]) == 2
    assert body["steps"][1]["txHash"] and "green" in body["steps"][1]["label"] and "Attested" in body["steps"][1]["label"]
    m = CERT_RE.fullmatch(body["certificatePath"])
    assert m and int(m.group(1)) == body["batchId"] == 1
    assert attestor.chain.batches[1]["status"] == "Attested"


def test_red(client, attestor):
    body = run(client, "red").json()
    assert_shape(body, "red")
    assert all(s["ok"] and s["expected"] for s in body["steps"])
    assert [bool(s["txHash"]) for s in body["steps"]] == [False, True, False, True]
    assert "PendingVerifier" in body["steps"][1]["label"] and "Rejected" in body["steps"][3]["label"]
    b = attestor.chain.batches[body["batchId"]]
    assert b["status"] == "Rejected" and b["verifier"] == ADDR["verifier"] and b["reasonHash"] != bytes(32)
    reason = attestor.get_reason("0x" + b["reasonHash"].hex())["text"]
    assert "rejected by the verifier" in reason
    assert CERT_RE.fullmatch(body["certificatePath"])


def test_double_claim(client, attestor):
    body = run(client, "double-claim").json()
    assert_shape(body, "double-claim")
    attest, alloc_a, alloc_b = body["steps"][1], body["steps"][2], body["steps"][3]
    assert attest["ok"] and alloc_a["ok"] and alloc_a["expected"] and alloc_a["txHash"]
    assert "3,000 of 5,000" in alloc_a["label"]
    # the over-allocation: fails on-chain (ok=false) exactly as designed (expected=true), with a mined tx hash
    assert alloc_b["ok"] is False and alloc_b["expected"] is True and alloc_b["txHash"]
    batch_id = body["batchId"]
    assert alloc_b["detail"] == f"DoubleClaimBlocked(batchId={batch_id}, requested=3000, remaining=2000)"
    revert, gas = attestor.chain.failed_txs[-1]
    assert revert.name == "DoubleClaimBlocked" and gas == 300_000  # fixed gas limit -> mined with status 0
    assert attestor.chain.allocated[batch_id] == 3000  # the failed allocation changed nothing
    assert len(body["steps"]) == 4 and CERT_RE.fullmatch(body["certificatePath"])


def test_a_step_that_goes_wrong_is_reported_not_raised(client, attestor):
    attestor.chain.roles.discard(("OPERATOR_ROLE", ADDR["exporterA"]))
    r = run(client, "green")
    assert r.status_code == 200
    body = r.json()
    assert body["batchId"] is None and body["certificatePath"] is None
    last = body["steps"][-1]
    assert last["ok"] is False and last["expected"] is False and "OPERATOR_ROLE" in last["detail"]


def test_wrong_verifier_key_surfaces_as_a_failed_step(attestor, demo_cfg):
    cfg = dataclasses.replace(demo_cfg, demo=dataclasses.replace(demo_cfg.demo, verifier_key=KEYS["importerA"]))
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        body = run(c, "red").json()
    assert body["steps"][-1]["ok"] is False and body["steps"][-1]["expected"] is False
    assert "MissingRole" in body["steps"][-1]["detail"]


def test_exporter_key_must_match_the_installation_operator(attestor, demo_cfg):
    cfg = dataclasses.replace(demo_cfg, demo=dataclasses.replace(demo_cfg.demo, exporter_b_key=None))
    with TestClient(create_app(attestor=attestor, cfg=cfg)) as c:
        body = run(c, "red").json()  # TR-BOF-0002 is operated by exporter B, whose key is not configured
    assert body["batchId"] is None and "none of the configured demo exporter keys" in body["steps"][-1]["detail"]


# ------------------------------------------------------------------ concurrency and cooldown


def test_second_run_while_one_is_in_progress_is_409(client):
    runner = client.app.state.demo_runner
    assert runner.lock.acquire(blocking=False)
    try:
        r = run(client, "green")
        assert r.status_code == 409 and "in progress" in r.json()["detail"]
    finally:
        runner.lock.release()
    assert run(client, "green").status_code == 200  # and the lock really was released


def test_cooldown_is_429_with_retry_after_per_client_ip(client):
    assert run(client, "green", ip="203.0.113.5").status_code == 200
    r = run(client, "green", ip="203.0.113.5")
    assert r.status_code == 429 and "cooldown" in r.json()["detail"]
    assert 1 <= int(r.headers["retry-after"]) <= 30
    assert run(client, "green", ip="203.0.113.6").status_code == 200  # another client is unaffected
    assert run(client, "green", ip="2001:db8:5:5::1").status_code == 200
    assert run(client, "green", ip="2001:db8:5:5::2").status_code == 429  # same /64 shares the cooldown


def test_a_rejected_request_does_not_start_a_run_or_use_gas(client, attestor):
    run(client, "green", ip="203.0.113.9")
    before = len(attestor.chain.batches)
    assert run(client, "double-claim", ip="203.0.113.9").status_code == 429
    assert len(attestor.chain.batches) == before


# ------------------------------------------------------------------ period choice


def test_green_runs_consume_one_period_each_going_backwards(demo_cfg, attestor):
    runner = DemoRunner(attestor=attestor, cfg=demo_cfg.demo)
    for _ in range(3):
        assert runner.run("green")["steps"][-1]["ok"]
    periods = sorted(b["period"] for b in attestor.chain.batches.values())
    assert periods == [202607, 202608, 202609]


def test_red_runs_reuse_the_rejected_slot(demo_cfg, attestor):
    runner = DemoRunner(attestor=attestor, cfg=demo_cfg.demo)
    for _ in range(3):
        assert runner.run("red")["steps"][-1]["expected"]
    assert [b["period"] for b in attestor.chain.batches.values()] == [202609] * 3
    assert [b["status"] for b in attestor.chain.batches.values()] == ["Rejected"] * 3


def test_never_picks_a_future_month_and_gives_up_cleanly(demo_cfg, attestor):
    assert clamp_to_current_month("2026-09", 2027, 5) == "2026-09"
    assert clamp_to_current_month("2030-01", 2027, 5) == "2027-05"
    rep = load_report("honest_eaf.json")
    assert list(months_back("2026-02", 3)) == ["2026-02", "2026-01", "2025-12", "2025-11"]
    with pytest.raises(LookupError):
        free_period(lambda i, p: 1, lambda b: "Attested", rep, max_back=5)
    assert free_period(lambda i, p: 0 if p == 202603 else 1, lambda b: "Attested", rep)["period"] == "2026-03"


def test_period_search_window_is_about_twenty_five_years():
    assert MAX_MONTHS_BACK == 300
    assert list(months_back("2026-09", MAX_MONTHS_BACK))[-1] == "2001-09"


# ------------------------------------------------------------------ bands must not move with the period (real model)

INSTALLATIONS = {"honest_eaf.json": ({"id": "TR-EAF-0001", "route": "EAF", "capacityTpy": 300000, "country": "TR"}, "green"),
                 "implausible_bof.json": ({"id": "TR-BOF-0002", "route": "BF-BOF", "capacityTpy": 3500000, "country": "TR"}, "red")}


def band_sample(report_period: str):
    """Every month of the report's own year (the model reads only the year of the period, so months within a year must be
    identical) plus January of every year in the runner's search window. Scoring all 301 months would take ~100 s because
    of the SHAP explanation."""
    window = list(months_back(report_period, MAX_MONTHS_BACK))
    year = report_period[:4]
    return sorted({p for p in window if p[:4] == year or p.endswith("-01")} | {window[-1]})


@pytest.mark.parametrize("name", list(INSTALLATIONS))
def test_every_period_the_runner_can_pick_keeps_the_demo_band(real_scorer, name):
    """The runner walks back up to MAX_MONTHS_BACK months to find an unused slot; the real model must give the identical
    score, interval and band for those periods (green stays green, red stays red)."""
    installation, band = INSTALLATIONS[name]
    rep = load_report(name)
    base = real_scorer.score_report(rep, installation)
    assert base["band"] == band
    sample = band_sample(rep["period"])
    assert len(sample) >= 30  # Jan-Sep 2026 + every January back to 2002 + the window end
    for period in sample:
        s = real_scorer.score_report({**rep, "period": period}, installation)
        assert (s["riskScore"], s["band"], s["q05"], s["q50"], s["q95"]) == (
            base["riskScore"], band, base["q05"], base["q50"], base["q95"]), period


def test_runner_with_the_real_model_green_stays_green_red_stays_red(real_attestor, demo_cfg):
    runner = DemoRunner(attestor=real_attestor, cfg=demo_cfg.demo)
    for _ in range(3):
        assert all(s["ok"] and s["expected"] for s in runner.run("green")["steps"])
    for _ in range(2):
        body = runner.run("red")
        assert all(s["ok"] and s["expected"] for s in body["steps"]), body["steps"]
    body = runner.run("double-claim")
    assert all(s["expected"] for s in body["steps"]) and body["steps"][-1]["ok"] is False
    bands = [(b["installationIdStr"], b["riskScore"] > 60) for b in real_attestor.chain.batches.values()]
    assert all(red == (iid == "TR-BOF-0002") for iid, red in bands)
