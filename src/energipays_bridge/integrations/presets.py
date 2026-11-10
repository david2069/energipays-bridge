"""Starting points for known sources (#15, phase C).

Each preset pre-fills protocol, config and mappings — signs and value maps included —
from what the source's own maintainers reported. Everything stays editable, and the UI
asks the user to confirm with Test: one report is a claim, not a measurement.

``placeholders`` are substituted into every mapping ``source`` (and ``config`` string)
when the user applies the preset.

Sources (claims by the sibling sessions, 2026-11-10, recorded on #15):
- franklinwh-modbus-bridge v0.2.62: REST/MQTT keys, signs (+discharge, +import), MQTT in kW,
  battery_state Charging/Discharging/Idle, mode names "Emergency Backup"/"Self-Consumption"/"TOU".
- franklinwh-direct-connect-bridge main: REST summary power.* keys, signs (+discharge,
  +import), battery_status charging/discharging/standby, mode keys tou/self/backup,
  status_grid_status Connected/SimulatedOffGrid/Outage/OffGrid.
- modbus-bridge (generic SunSpec, unpublished): REST points.{key}.value, 714.DCW +discharge,
  label.battery_operating_mode 1/2/3, label.battery_inverter_state.
"""
from __future__ import annotations

from typing import Any

# DC bridge grid status → bridge grid connection. SimulatedOffGrid is a test mode the
# DC bridge reports while the grid is in fact present, so it is not mapped to off_grid.
_DC_GRID = {"Connected": "on_grid", "OffGrid": "off_grid", "Outage": "off_grid"}

# franklinwh-modbus-bridge grid connection text → bridge state
_MB_GRID = {"Connected": "on_grid", "Disconnected": "off_grid"}

PRESETS: list[dict[str, Any]] = [
    # franklinwh-modbus-bridge has no REST preset: its REST API takes only a login session
    # cookie (14 days), which this bridge's REST poller cannot hold. Use MQTT.
    {
        "id": "fwh-modbus-bridge-mqtt",
        "name": "FranklinWH Modbus Bridge — MQTT",
        "source": "franklinwh-modbus-bridge v0.2.62 docs/dev/mqtt.md (plain-text retained values, kW)",
        "type": "battery",
        "protocol": "mqtt",
        "config": {"host": "", "port": 1883},
        "placeholders": {
            "prefix": "fwh_mb (older installs: franklinwh)",
            "short_id": "last 8 characters of the aGate serial (default gateway)",
        },
        "mappings": [
            {"source": "{prefix}/{short_id}/battery/battery_power_kw", "target_metric": "battery_power_w",
             "scale": 1000, "positive": "discharge"},
            {"source": "{prefix}/{short_id}/power/grid_power_kw", "target_metric": "grid_power_w",
             "scale": 1000, "positive": "import"},
            {"source": "{prefix}/{short_id}/power/home_load_kw", "target_metric": "home_load_w", "scale": 1000},
            {"source": "{prefix}/{short_id}/solar/solar_power_kw", "target_metric": "solar_power_w", "scale": 1000},
            {"source": "{prefix}/{short_id}/battery/battery_soc", "target_metric": "battery_soc"},
            {"source": "{prefix}/{short_id}/battery/battery_soh", "target_metric": "battery_soh"},
            {"source": "{prefix}/{short_id}/battery/battery_state", "target_metric": "battery_state"},
            {"source": "{prefix}/{short_id}/status/operating_mode_sensor", "target_metric": "operating_mode"},
            {"source": "{prefix}/{short_id}/status/grid_connection_state", "target_metric": "battery_grid_status",
             "value_map": _MB_GRID},
            # Topics seen live on that bridge's Demo gateway, 2026-11-10 (#15 phase D)
            {"source": "{prefix}/{short_id}/control/self_reserve_pct", "target_metric": "self_reserve_soc"},
            {"source": "{prefix}/{short_id}/control/tou_reserve_pct", "target_metric": "tou_reserve_soc"},
            {"source": "{prefix}/{short_id}/capacity/total_capacity_kwh", "target_metric": "battery_capacity_kwh"},
            {"source": "{prefix}/{short_id}/status/inverter_state", "target_metric": "inverter_state"},
            {"source": "{prefix}/{short_id}/status/ambient_temp_c", "target_metric": "ambient_temp_c"},
            {"source": "{prefix}/{short_id}/status/cabinet_temp_c", "target_metric": "cabinet_temp_c"},
        ],
    },
    {
        "id": "dc-bridge-mqtt",
        "name": "FranklinWH Direct Connect Bridge — MQTT",
        "source": "franklinwh-direct-connect-bridge publish/entities.py state_payload() (one flat JSON payload)",
        "type": "battery",
        "protocol": "mqtt",
        "config": {"host": "", "port": 1883},
        "placeholders": {"node": "the aGate serial, lower case"},
        "mappings": [
            {"source": "franklinwh-local/{node}/state:battery_w", "target_metric": "battery_power_w",
             "positive": "discharge"},
            {"source": "franklinwh-local/{node}/state:grid_w", "target_metric": "grid_power_w", "positive": "import"},
            {"source": "franklinwh-local/{node}/state:solar_w", "target_metric": "solar_power_w"},
            {"source": "franklinwh-local/{node}/state:load_w", "target_metric": "home_load_w"},
            {"source": "franklinwh-local/{node}/state:generator_w", "target_metric": "extra_load_w"},
            {"source": "franklinwh-local/{node}/state:soc", "target_metric": "battery_soc"},
            {"source": "franklinwh-local/{node}/state:battery_status", "target_metric": "battery_state"},
            {"source": "franklinwh-local/{node}/state:mode", "target_metric": "operating_mode"},
            {"source": "franklinwh-local/{node}/state:status_grid_status", "target_metric": "battery_grid_status",
             "value_map": _DC_GRID},
        ],
    },
    {
        "id": "dc-bridge-rest",
        "name": "FranklinWH Direct Connect Bridge — REST",
        "source": "franklinwh-direct-connect-bridge app.py:2755, poller.py:131-153; auth.py",
        "type": "battery",
        "protocol": "rest",
        # Security off (default): no header, trusted networks only. Security on: HTTPS and a
        # static API token as Bearer.
        "config": {"base_url": "", "endpoint": "/api/gateways/{gateway_id}/summary", "auth_type": "none"},
        "placeholders": {"gateway_id": "the gateway id shown in the DC Bridge"},
        "mappings": [
            {"source": "power.battery_w", "target_metric": "battery_power_w", "positive": "discharge"},
            {"source": "power.grid_w", "target_metric": "grid_power_w", "positive": "import"},
            {"source": "power.solar_w", "target_metric": "solar_power_w"},
            {"source": "power.load_w", "target_metric": "home_load_w"},
            {"source": "power.generator_w", "target_metric": "extra_load_w"},
            {"source": "power.soc", "target_metric": "battery_soc"},
            {"source": "power.mode", "target_metric": "operating_mode"},
        ],
    },
    {
        "id": "modbus-bridge-rest",
        "name": "Modbus Bridge (SunSpec) — REST",
        "source": "modbus-bridge src/modbus_bridge/api.py, quirk_sets/franklinwh.json (static Bearer token)",
        "type": "battery",
        "protocol": "rest",
        "config": {"base_url": "", "endpoint": "/api/v1/devices/{device_id}/points", "auth_type": "bearer"},
        "placeholders": {"device_id": "the device id shown in the Modbus Bridge"},
        "mappings": [
            {"source": "points.714.DCW.value", "target_metric": "battery_power_w", "positive": "discharge"},
            {"source": "points.713.SoC.value", "target_metric": "battery_soc"},
            {"source": "points.label.home_load_power.value", "target_metric": "home_load_w"},
            {"source": "points.label.solar_total_power.value", "target_metric": "solar_power_w"},
            {"source": "points.label.battery_operating_mode.value", "target_metric": "operating_mode",
             "value_map": {"1": "emergency_backup", "2": "self_consumption", "3": "time_of_use"}},
        ],
    },
    {
        "id": "modbus-bridge-mqtt",
        "name": "Modbus Bridge (SunSpec) — MQTT",
        "source": "modbus-bridge src/modbus_bridge/mqtt_publisher.py (slug topics, plain-text values)",
        "type": "battery",
        "protocol": "mqtt",
        "config": {"host": "", "port": 1883},
        "placeholders": {"serial": "the device serial, lower case, other characters as _"},
        "mappings": [
            {"source": "modbus_bridge/{serial}/m714_dcw/state", "target_metric": "battery_power_w",
             "positive": "discharge"},
            {"source": "modbus_bridge/{serial}/m713_soc/state", "target_metric": "battery_soc"},
            {"source": "modbus_bridge/{serial}/label_home_load_power/state", "target_metric": "home_load_w"},
            {"source": "modbus_bridge/{serial}/label_solar_total_power/state", "target_metric": "solar_power_w"},
            {"source": "modbus_bridge/{serial}/label_battery_operating_mode/state", "target_metric": "operating_mode",
             "value_map": {"1": "emergency_backup", "2": "self_consumption", "3": "time_of_use"}},
        ],
    },
]


def list_presets() -> list[dict[str, Any]]:
    return PRESETS


def apply(preset_id: str, values: dict[str, str]) -> dict[str, Any]:
    """Preset with placeholders filled in, ready to save as an integration."""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if preset is None:
        raise KeyError(preset_id)
    missing = [k for k in preset.get("placeholders", {}) if not str(values.get(k, "")).strip()]
    if missing:
        raise ValueError(f"fill in: {', '.join(missing)}")

    def fill(s: str) -> str:
        for k, v in values.items():
            s = s.replace("{" + k + "}", str(v).strip())
        return s
    config = {k: fill(v) if isinstance(v, str) else v for k, v in preset["config"].items()}
    mappings = [{**m, "source": fill(m["source"])} for m in preset["mappings"]]
    return {"name": preset["name"], "type": preset["type"], "protocol": preset["protocol"],
            "config": config, "mappings": mappings}
