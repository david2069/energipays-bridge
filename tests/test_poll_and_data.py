"""Poller and data handling: statistics (#4), PII (#7), errors/is_online/data server (#8),
integrations (#10)."""
import logging

import pytest
from fastapi.testclient import TestClient

from conftest import DEVICE, FakeClient, run
from energipays_bridge.device_lookup import resolve_device
from energipays_bridge.poller import EnergipaysPoller, _flatten
from energipays_bridge.sample import Sample, SampleBus

PROFILE = {"data": {"status_data": {"customer": 1}, "user": {
    "email": "user@example.com", "name": "A", "last_name": "B",
    "address": "1 Example St", "city": "Sydney", "state": "NSW", "country": "AU",
    "zip": "2000", "phone_number": "0400000000"}}}


def test_fortnight_feeds_last14days():
    pts = _flatten({}, {"fortnight": {"EEct": 3.5}, "today": {"EEct": 1}})
    assert pts["last14days.EEct"] == 3.5 and pts["today.EEct"] == 1


def test_no_address_or_phone_points():
    pts = _flatten({}, {}, PROFILE)
    assert pts["user.email"] == "user@example.com"
    for k in ("user.address", "user.city", "user.state", "user.country", "user.zip",
              "user.phone_number"):
        assert k not in pts


def test_is_online_read_from_top_level():
    st = {"data": [{"id": DEVICE, "is_online": 0, "status": {"waterTemperature1": 50}}]}
    assert _flatten(st, {})["is_online"] == 0


def test_is_online_falls_back_to_telemetry():
    st = {"data": [{"id": DEVICE, "status": {"is_online": 1}}]}
    assert _flatten(st, {})["is_online"] == 1


def _poller(client):
    bus = SampleBus()
    got = []

    async def sub(s):
        got.append(s)
    bus.subscribe(sub)
    return EnergipaysPoller(client, bus, DEVICE, "https://data-x", tz="Australia/Perth"), got


def test_status_error_counts_as_failed_poll():
    c = FakeClient(device_status={"error": "401", "status": 401})
    p, got = _poller(c)
    run(p._poll())
    assert p.consecutive_failures == 1 and p.polls_total == 0 and not p.connected
    assert got[0].quality == "error"


def test_statistics_error_is_left_out_not_failed():
    c = FakeClient(device_status={"data": [{"is_online": 1, "status": {}}]},
                   statistics={"error": "500", "status": 500}, device={})
    p, got = _poller(c)
    run(p._poll())
    assert p.polls_total == 1 and got[0].quality == "ok"
    assert not any(k.startswith("today.") for k in got[0].points)


def test_statistics_use_the_configured_timezone():
    c = FakeClient(device_status={"data": []}, statistics={}, device={})
    p, _ = _poller(c)
    run(p._poll())
    call = next(x for x in c.calls if x[0] == "statistics")
    assert call[1] == (DEVICE, "https://data-x", "Australia/Perth")


def test_configured_device_takes_its_server_from_the_list():
    resp = {"data": [{"id": "a", "server": "https://data-1"}, {"id": "b", "server": "https://data-2"}]}
    assert resolve_device(resp, "b") == ("b", "https://data-2")
    assert resolve_device(resp, "") == ("a", "https://data-1")
    assert resolve_device(resp, "zzz")[1] == "https://data-au-1.energipays.com"


def test_emails_are_masked_in_setup_logs(caplog, monkeypatch):
    from energipays_bridge.api import setup
    import energipays

    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(energipays.EnergipaysClient, "_ensure_key", boom)
    with caplog.at_level(logging.DEBUG):
        run(setup._test_login("user@example.com", "x"))
    assert "user@example.com" not in caplog.text and "u•••r@example.com" in caplog.text


# ── cloud stats + local ranges (#4) ─────────────────────────────────────────

@pytest.mark.parametrize("sent, data_type, phase", [
    ({"data_type": "energy", "phase": "l2"}, "consumption", "b"),
    ({"data_type": "power", "phase": "sum"}, "power", "sum"),
    ({"data_type": "consumption", "phase": "c"}, "consumption", "c"),
])
def test_cloud_stats_sends_the_sites_values(app_factory, sent, data_type, phase):
    c = FakeClient()
    r = TestClient(app_factory(c)).get("/api/cloud/stats", params={
        "date_from": "2026-01-01", "date_to": "2026-01-01", **sent})
    assert r.status_code == 200
    args = c.calls[0][1]
    assert args[4] == "Australia/Perth" and args[5] == data_type and args[6] == phase


def test_cloud_stats_rejects_unknown_values(app_factory):
    r = TestClient(app_factory(FakeClient())).get("/api/cloud/stats", params={
        "date_from": "2026-01-01", "date_to": "2026-01-01", "phase": "z"})
    assert r.status_code == 400


@pytest.mark.parametrize("rng, seconds", [("8h", 8 * 3600), ("18h", 18 * 3600),
                                           ("3d", 3 * 86400), ("14d", 14 * 86400),
                                           ("30m", 1800)])
def test_every_ui_range_is_known(rng, seconds):
    from energipays_bridge.api.metrics import _RANGE_SECONDS
    assert _RANGE_SECONDS[rng] == seconds


# ── integrations (#10) ──────────────────────────────────────────────────────

def _registry(tmp_path):
    from energipays_bridge.integrations.registry import IntegrationRegistry
    from energipays_bridge.store.db import init_db

    async def make():
        db = await init_db(tmp_path / "t.db")
        return IntegrationRegistry(db, SampleBus())
    return make


def test_stopping_an_integration_drops_only_its_values(tmp_path):
    async def go():
        reg = await _registry(tmp_path)()
        await reg._on_sample("a", Sample("ext", 0, {"ext.battery_soc": 50, "ext.shared": 1}))
        await reg._on_sample("b", Sample("ext", 0, {"ext.solar_power_w": 900, "ext.shared": 2}))
        await reg._stop_one("a")
        await reg.db.close()
        return reg.latest
    latest = run(go())
    assert "ext.battery_soc" not in latest
    assert latest["ext.solar_power_w"] == 900 and "ext.shared" in latest


def test_disable_and_delete_clear_values(tmp_path):
    async def go():
        reg = await _registry(tmp_path)()
        row = await reg.create({"name": "m", "type": "battery", "protocol": "modbus",
                                "config": {}, "mappings": [], "enabled": False})
        await reg._on_sample(row["id"], Sample("ext", 0, {"ext.battery_soc": 50}))
        await reg.set_enabled(row["id"], False)
        after_disable = dict(reg.latest)
        await reg._on_sample(row["id"], Sample("ext", 0, {"ext.battery_soc": 51}))
        await reg.delete(row["id"])
        await reg.db.close()
        return after_disable, reg.latest
    after_disable, after_delete = run(go())
    assert "ext.battery_soc" not in after_disable and "ext.battery_soc" not in after_delete


def test_update_without_enabled_keeps_it_disabled(tmp_path):
    async def go():
        reg = await _registry(tmp_path)()
        row = await reg.create({"name": "m", "type": "battery", "protocol": "modbus",
                                "config": {}, "mappings": [], "enabled": False})
        out = await reg.update(row["id"], {"name": "m2", "type": "battery",
                                           "protocol": "modbus", "config": {}, "mappings": []})
        await reg.db.close()
        return out
    assert run(go())["enabled"] is False
