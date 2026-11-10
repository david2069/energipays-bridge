"""Presets for known sources (#15, phase C).

Sample payloads follow the shapes the source maintainers described (claims recorded
on #15); they are not captures.
"""
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from energipays_bridge.integrations.models import FieldMapping, IntegrationIn
from energipays_bridge.integrations.normalise import convert
from energipays_bridge.integrations.presets import PRESETS, apply
from energipays_bridge.integrations.rest_poller import _dotpath, _extract_mappings

VALUES = {"prefix": "fwh_mb", "short_id": "abcd1234", "node": "fwh00000000",
          "gateway_id": "default", "device_id": "dev1", "serial": "sn0001"}


def filled(preset_id):
    return apply(preset_id, VALUES)


# ── dot paths with dotted keys ──────────────────────────────────────────────

def test_dotpath_finds_keys_that_contain_dots():
    data = {"points": {"714.DCW": {"value": -2000}, "label.home_load_power": {"value": 900}}}
    assert _dotpath(data, "points.714.DCW.value") == -2000
    assert _dotpath(data, "points.label.home_load_power.value") == 900


def test_dotpath_plain_and_list_paths_unchanged():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert _dotpath(data, "a.b.1.c") == 2
    assert _dotpath(data, "a.x") is None
    assert _dotpath(data, "a.b.9.c") is None


def test_dotpath_prefers_the_longest_matching_key():
    data = {"a.b": {"c": "long"}, "a": {"b": {"c": "short"}}}
    assert _dotpath(data, "a.b.c") == "long"


# ── every preset is a valid integration ─────────────────────────────────────

@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
def test_preset_applies_to_a_valid_integration(preset):
    out = filled(preset["id"])
    IntegrationIn(**out, enabled=False)          # validates signs, protocol, mappings
    assert not re.search(r"\{[a-z_]+\}", json.dumps(out)), "placeholder left unfilled"


def test_missing_placeholder_is_refused():
    with pytest.raises(ValueError, match="short_id"):
        apply("fwh-modbus-bridge-mqtt", {"prefix": "fwh_mb"})


def test_no_rest_preset_for_cookie_only_source():
    # franklinwh-modbus-bridge REST needs a session cookie the REST poller can't hold
    assert not any(p["id"] == "fwh-modbus-bridge-rest" for p in PRESETS)


# ── sample payloads through the preset mappings ─────────────────────────────

def _mqtt(preset_id, messages):
    """messages: topic -> payload text. Mirrors MqttSubscriber's per-mapping handling."""
    out = {}
    for m in filled(preset_id)["mappings"]:
        fm = FieldMapping(**m)
        topic, _, path = fm.source.partition(":")
        if topic not in messages:
            continue
        payload = messages[topic]
        raw = _dotpath(json.loads(payload), path) if path else payload
        if raw is not None:
            out[fm.target_metric] = convert(fm, raw)
    return out


def test_fwh_modbus_bridge_mqtt_kw_text_states():
    t = "fwh_mb/abcd1234/"
    out = _mqtt("fwh-modbus-bridge-mqtt", {
        t + "battery/battery_power_kw": "-1.25", t + "power/grid_power_kw": "0.4",
        t + "battery/battery_soc": "54.2", t + "battery/battery_state": "Idle",
        t + "status/operating_mode_sensor": "TOU",
        t + "status/grid_connection_state": "Disconnected"})
    assert out == {"battery_power_w": -1250, "grid_power_w": 400, "battery_soc": 54.2,
                   "battery_state": "standby", "operating_mode": "time_of_use",
                   "battery_grid_status": "off_grid"}


def test_dc_bridge_mqtt_flat_json_payload():
    payload = json.dumps({"battery_w": 1800, "grid_w": -250, "soc": 71, "mode": "Self-Consumption",
                          "battery_status": "discharging", "status_grid_status": "Outage"})
    out = _mqtt("dc-bridge-mqtt", {"franklinwh-local/fwh00000000/state": payload})
    assert out == {"battery_power_w": 1800, "grid_power_w": -250, "battery_soc": 71,
                   "operating_mode": "self_consumption", "battery_state": "discharging",
                   "battery_grid_status": "off_grid"}


def test_dc_bridge_simulated_off_grid_is_not_reported_as_off_grid():
    payload = json.dumps({"status_grid_status": "SimulatedOffGrid"})
    out = _mqtt("dc-bridge-mqtt", {"franklinwh-local/fwh00000000/state": payload})
    assert out["battery_grid_status"] != "off_grid"


def test_dc_bridge_rest_summary_and_mode_keys():
    data = {"power": {"battery_w": -600, "grid_w": 1200, "soc": 40, "mode": "self"}}
    maps = [FieldMapping(**m) for m in filled("dc-bridge-rest")["mappings"]]
    out = _extract_mappings(data, maps)
    assert out == {"battery_power_w": -600, "grid_power_w": 1200, "battery_soc": 40,
                   "operating_mode": "self_consumption"}


def test_modbus_bridge_rest_dotted_point_keys():
    data = {"points": {"714.DCW": {"value": -2000}, "713.SoC": {"value": 87.5},
                       "label.battery_operating_mode": {"value": 3}}}
    maps = [FieldMapping(**m) for m in filled("modbus-bridge-rest")["mappings"]]
    out = _extract_mappings(data, maps)
    assert out == {"battery_power_w": -2000, "battery_soc": 87.5, "operating_mode": "time_of_use"}


# ── API ─────────────────────────────────────────────────────────────────────

def test_preset_api_list_apply_and_errors():
    from energipays_bridge.api import integrations
    app = FastAPI()
    app.include_router(integrations.router)
    c = TestClient(app)
    ids = [p["id"] for p in c.get("/api/integrations/presets").json()]
    assert "dc-bridge-mqtt" in ids
    r = c.post("/api/integrations/presets/dc-bridge-rest/apply", json={"gateway_id": "g1"})
    assert r.status_code == 200 and r.json()["config"]["endpoint"] == "/api/gateways/g1/summary"
    assert c.post("/api/integrations/presets/dc-bridge-rest/apply", json={}).status_code == 400
    assert c.post("/api/integrations/presets/nope/apply", json={}).status_code == 404


# ── self-signed HTTPS sources (#25) ─────────────────────────────────────────

def test_rest_poller_verify_tls_follows_config(monkeypatch):
    import asyncio
    import httpx
    from energipays_bridge.integrations.registry import _build_poller
    from energipays_bridge.sample import SampleBus

    seen = []

    class FakeClient:
        def __init__(self, *a, **k):
            seen.append(k.get("verify", True))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(200, json={"power": {"battery_w": 1}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    for cfg, want in (({}, True), ({"verify_tls": False}, False), ({"verify_tls": True}, True)):
        row = {"id": "r", "name": "n", "protocol": "rest", "enabled": True,
               "config": {"base_url": "https://192.0.2.7:18791", "endpoint": "/x", **cfg},
               "mappings": [{"source": "power.battery_w", "target_metric": "battery_power_w"}]}
        asyncio.run(_build_poller(row, SampleBus())._poll())
        assert seen[-1] is want
