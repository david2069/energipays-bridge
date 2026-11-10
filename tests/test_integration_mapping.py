"""One sign and state mapping for every integration protocol (#15)."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from energipays_bridge.integrations import modbus_poller
from energipays_bridge.integrations.base import publish_points
from energipays_bridge.integrations.models import FieldMapping
from energipays_bridge.integrations.normalise import convert, derive, state_code
from energipays_bridge.integrations.rest_poller import _extract_mappings
from energipays_bridge.sample import SampleBus


def fm(metric, **kw):
    return FieldMapping(source=kw.pop("source", "x"), target_metric=metric, **kw)


# ── Signs ────────────────────────────────────────────────────────────────────

def test_battery_default_is_positive_discharge():
    assert convert(fm("battery_power_w"), 1200) == 1200


def test_battery_source_positive_charge_is_flipped():
    assert convert(fm("battery_power_w", positive="charge"), 1200) == -1200


def test_grid_source_positive_export_is_flipped():
    assert convert(fm("grid_power_w", positive="export"), "300") == -300
    assert convert(fm("grid_power_w", positive="import"), "300") == 300


def test_scale_and_sign_combine_for_kw_sources():
    # franklinwh-modbus-bridge MQTT publishes kW
    assert convert(fm("battery_power_w", scale=1000, positive="charge"), 1.5) == -1500


def test_negative_scale_migrates_to_explicit_sign():
    m = fm("battery_power_w", scale=-1)
    assert (m.positive, m.scale) == ("charge", 1)
    assert convert(m, 500) == -500
    again = FieldMapping(**m.model_dump())     # re-validating doesn't flip back
    assert (again.positive, again.scale) == ("charge", 1)


def test_sign_rejected_on_unsigned_metric_and_bad_value():
    with pytest.raises(ValidationError):
        fm("solar_power_w", positive="import")
    with pytest.raises(ValidationError):
        fm("battery_power_w", positive="import")


# ── States ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("Idle", "standby"), ("Charging", "charging"), ("Discharging", "discharging"),
    ("standby", "standby"),
])
def test_battery_state_text_from_any_source(raw, want):
    assert convert(fm("battery_state"), raw) == want


@pytest.mark.parametrize("raw,want", [
    (1, "emergency_backup"), ("2", "self_consumption"), (3.0, "time_of_use"),
    ("TOU", "time_of_use"), ("Emergency Backup", "emergency_backup"),
    ("Self-Consumption", "self_consumption"), ("time_of_use", "time_of_use"),
])
def test_operating_mode_codes_and_text(raw, want):
    assert convert(fm("operating_mode"), raw) == want


def test_value_map_wins_over_defaults():
    m = fm("operating_mode", value_map={"1": "time_of_use"})
    assert convert(m, 1) == "time_of_use"
    assert convert(m, "1.0") == "time_of_use"


def test_grid_connection_has_no_default_codes():
    # Sources disagree on these codes, so a bare number is passed through unchanged
    assert convert(fm("battery_grid_status"), 2) == 2
    assert convert(fm("battery_grid_status", value_map={"2": "on_grid"}), 2) == "on_grid"


@pytest.mark.parametrize("raw,want", [
    (0, "grid_following"), (1, "grid_following"), (2, "grid_forming"),
    (4, "pv_clipped"), (6, "pv_clipped"), (3, "grid_forming"),
])
def test_der_mode_bit_priority(raw, want):
    assert convert(fm("grid_status"), raw) == want


def test_inverter_state_full_sunspec_enum():
    assert convert(fm("inverter_state"), 3) == "running"
    assert convert(fm("inverter_state"), 9) == "manufacturing"
    assert convert(fm("inverter_state"), 42) == "unknown_42"
    assert convert(fm("connection_state"), 2) == "fault"


# ── Derivation and storage ──────────────────────────────────────────────────

@pytest.mark.parametrize("pw,want", [(51, "discharging"), (-51, "charging"), (50, "standby"), (0, "standby")])
def test_battery_state_derived_from_internal_power(pw, want):
    assert derive({"battery_power_w": pw})["battery_state"] == want


def test_source_battery_state_is_kept():
    assert derive({"battery_power_w": 900, "battery_state": "standby"})["battery_state"] == "standby"


def test_state_codes_are_stable():
    assert state_code("battery_state", "charging") == 1
    assert state_code("operating_mode", "time_of_use") == 3
    assert state_code("battery_state", "nonsense") is None
    assert state_code("solar_power_w", "x") is None


def test_recorder_stores_states_as_codes():
    from energipays_bridge.sample import Sample
    from energipays_bridge.store.metrics import MetricsRecorder

    rows = []

    class DB:
        async def executemany(self, sql, r):
            rows.extend(r)

        async def commit(self):
            pass

    sample = Sample(device_id="ext", ts=1.0, points={
        "ext.battery_state": "discharging", "ext.battery_power_w": 700.0, "ext.note": "free text"})
    asyncio.run(MetricsRecorder(DB())(sample))
    got = {r[1]: r[3] for r in rows}
    assert got == {"ext.battery_state": 2.0, "ext.battery_power_w": 700.0}


# ── Every protocol takes the same path ──────────────────────────────────────

def _collect(points):
    bus = SampleBus()
    seen = []

    async def go():
        async def sub(s):
            seen.append(s.points)
        bus.subscribe(sub)
        await publish_points(bus, points)
    asyncio.run(go())
    return seen[-1]


def test_rest_source_with_charge_positive_and_idle_text():
    data = {"points": {"battery_power_w": 800, "battery_state": "Idle"}}
    out = _extract_mappings(data, [
        fm("battery_power_w", source="points.battery_power_w", positive="charge"),
        fm("battery_state", source="points.battery_state")])
    assert out == {"battery_power_w": -800, "battery_state": "standby"}


def test_published_sample_has_derived_state():
    pts = _collect({"battery_power_w": -900.0})
    assert pts == {"ext.battery_power_w": -900.0, "ext.battery_state": "charging"}


def test_modbus_poller_uses_shared_conversion(monkeypatch):
    regs = {15012: 2000, 40100: 7}

    async def fake_read(host, port, unit, fc, address, count, reg_type, timeout=5.0):
        return [regs[address]]
    monkeypatch.setattr(modbus_poller, "read_registers", fake_read)
    p = modbus_poller.ModbusPoller("i", "n", SampleBus(), "192.0.2.10", 502, 1, [
        fm("battery_power_w", source="fc3:15012:int16:1", positive="charge"),
        fm("inverter_state", source="fc3:40100:uint16:1")])
    out = asyncio.run(p._poll())
    assert out == {"battery_power_w": -2000, "inverter_state": "standby"}


# ── API ─────────────────────────────────────────────────────────────────────

def _api():
    from energipays_bridge.api import integrations
    app = FastAPI()
    app.include_router(integrations.router)
    return TestClient(app), integrations


def test_labels_endpoint_describes_the_convention():
    client, _ = _api()
    d = client.get("/api/integrations/labels").json()
    assert d["signed"]["battery_power_w"] == ["discharge", "charge"]
    assert d["signed"]["grid_power_w"] == ["import", "export"]
    assert d["states"]["battery_state"]["standby"] == "Standby"


def test_probe_converts_like_polling(monkeypatch):
    client, integrations = _api()

    async def fake_read(*a, **k):
        return [3]
    monkeypatch.setattr(integrations, "read_registers", fake_read)
    d = client.post("/api/integrations/probe", json={
        "host": "192.0.2.10", "address": 1, "count": 1, "target_metric": "operating_mode"}).json()
    assert d["registers"] == [3]
    assert (d["value"], d["label"]) == ("time_of_use", "Time-of-Use")

    d = client.post("/api/integrations/probe", json={
        "host": "192.0.2.10", "address": 1, "count": 1, "target_metric": "battery_power_w",
        "positive": "charge"}).json()
    assert d["value"] == -3 and d["label"] is None


# ── State values with no known meaning (#30) ────────────────────────────────

def test_unmapped_battery_state_falls_back_to_power():
    # owner's screen: Battery State "-3" while discharging 0.20 kW
    raw = convert(fm("battery_state"), "-3")

    class Owner:
        unmapped = {}
    owner = Owner()
    bus = SampleBus()
    seen = []

    async def go():
        async def sub(s):
            seen.append(s.points)
        bus.subscribe(sub)
        await publish_points(bus, {"battery_power_w": 200.0, "battery_state": raw}, owner)
    asyncio.run(go())
    assert seen[-1]["ext.battery_state"] == "discharging"
    assert owner.unmapped == {"battery_state": -3.0}


def test_unmapped_state_without_power_is_not_published():
    pts = _collect({"operating_mode": 7.0, "inverter_state": "unknown_42", "battery_soc": 50})
    assert pts == {"ext.battery_soc": 50}


def test_known_states_and_plain_metrics_are_not_unmapped():
    from energipays_bridge.integrations.normalise import is_unmapped
    assert not is_unmapped("battery_state", "standby")
    assert not is_unmapped("battery_power_w", -3)
    assert not is_unmapped("battery_state", None)
    assert is_unmapped("battery_grid_status", 2)


def test_test_preview_flags_unmapped(monkeypatch):
    from energipays_bridge.api.integrations import _reading
    assert _reading(fm("battery_state"), -3)["unmapped"] is True
    assert _reading(fm("battery_state", value_map={"-3": "charging"}), -3)["unmapped"] is False
    assert _reading(fm("battery_power_w"), -3)["unmapped"] is False


def test_integration_card_lists_unmapped_values():
    from energipays_bridge.integrations.registry import IntegrationRegistry

    class Poller:
        status, last_error, unmapped = "live", "", {"battery_state": -3.0}
    reg = IntegrationRegistry.__new__(IntegrationRegistry)
    reg._pollers, reg._key = {"i": Poller()}, b"k" * 32
    row = {"id": "i", "enabled": True, "config": {}}
    assert reg._enrich(row)["unmapped"] == {"battery_state": -3}
    reg._pollers = {}
    assert reg._enrich(row)["unmapped"] == {}
