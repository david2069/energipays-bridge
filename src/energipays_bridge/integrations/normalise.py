"""One conversion from any integration source to the bridge's internal convention (#15).

Every protocol (REST, MQTT, HA WebSocket, Modbus TCP, SunSpec) passes each raw value
through :func:`convert`, then the whole point set through :func:`derive`. Nothing else
decodes, flips signs or invents labels.

Internal convention
-------------------
- ``battery_power_w``: **+ discharge / − charge** (SunSpec 714.DCW; franklinwh-modbus-bridge
  and modbus-bridge both publish it this way).
- ``grid_power_w``: **+ import / − export** (franklinwh-modbus-bridge 701.W; HA energy).
- Other powers are plain watts.
- State metrics hold a snake-case id from :data:`STATE_LABELS`; the UI shows its label.

Sources differ, so each mapping says what *its* positive sign means (``positive``) and
may carry a ``value_map`` from source codes/strings to internal ids.
"""
from __future__ import annotations

from typing import Any

# What a positive value means internally, per signed metric, and the alternative.
SIGNED_METRICS: dict[str, tuple[str, str]] = {
    "battery_power_w": ("discharge", "charge"),
    "grid_power_w": ("import", "export"),
}

# Deadband for deriving battery_state from power — the same 50 W both FranklinWH
# bridges use (franklinwh-modbus-bridge controller.py; this bridge since 1.1.10).
STANDBY_BAND_W = 50.0

# State metrics: internal id → display label. The order gives the stored code
# (index + 1), so append new ids at the end, never reorder.
STATE_LABELS: dict[str, dict[str, str]] = {
    "battery_state": {
        "charging": "Charging", "discharging": "Discharging", "standby": "Standby",
    },
    "operating_mode": {
        "emergency_backup": "Emergency Backup", "self_consumption": "Self-Consumption",
        "time_of_use": "Time-of-Use",
    },
    "battery_grid_status": {
        "on_grid": "On grid", "off_grid": "Off grid", "restarting": "Restarting",
        "dispatch_active": "Dispatch active",
    },
    "grid_status": {   # SunSpec 701.DERMode
        "grid_following": "Grid Following", "grid_forming": "Grid Forming",
        "pv_clipped": "PV Clipped",
    },
    "inverter_state": {   # SunSpec 701.InvSt
        "off": "Off", "sleeping": "Sleeping", "starting": "Starting", "running": "Running",
        "throttled": "Throttled", "shutting_down": "Shutting Down", "fault": "Fault",
        "standby": "Standby", "test": "Test", "manufacturing": "Manufacturing",
    },
    "battery_inverter_state": {
        "off": "Off", "sleeping": "Sleeping", "starting": "Starting", "running": "Running",
        "throttled": "Throttled", "shutting_down": "Shutting Down", "fault": "Fault",
        "standby": "Standby", "test": "Test", "manufacturing": "Manufacturing",
    },
    "connection_state": {   # SunSpec 701.ConnSt
        "disconnected": "Disconnected", "connected": "Connected", "fault": "Fault",
    },
}

_INV_ST = ["off", "sleeping", "starting", "running", "throttled", "shutting_down",
           "fault", "standby", "test", "manufacturing"]

# Numeric codes with one published meaning. Used only when the mapping has no
# value_map. Operating mode 1/2/3 is this bridge's documented convention (mapping UI
# and dashboard before #15) and modbus-bridge's register 15507. Grid-connection codes
# differ per source, so they have no default: the mapping must say.
_DEFAULT_CODES: dict[str, dict[int, str]] = {
    "operating_mode": {1: "emergency_backup", 2: "self_consumption", 3: "time_of_use"},
    "inverter_state": dict(enumerate(_INV_ST)),
    "battery_inverter_state": dict(enumerate(_INV_ST)),
    "connection_state": {0: "disconnected", 1: "connected", 2: "fault"},
}

# Text other sources publish for the same state (case-insensitive). Sources:
# franklinwh-modbus-bridge ("Idle", "Emergency Backup", "TOU"), modbus-bridge
# (snake-case ids), this bridge before #15 ("idle").
_ALIASES: dict[str, dict[str, str]] = {
    "battery_state": {"idle": "standby", "standby": "standby",
                      "charge": "charging", "discharge": "discharging"},
    "operating_mode": {"tou": "time_of_use", "time of use": "time_of_use",
                       "self consumption": "self_consumption", "backup": "emergency_backup",
                       "self": "self_consumption"},   # DC bridge mode keys tou/self/backup
}


def _snake(text: str) -> str:
    return "_".join(text.strip().lower().replace("-", " ").split())


def state_id(metric: str, raw: Any) -> Any:
    """Map a source state (text or number) to the internal id; unknown values pass through."""
    labels = STATE_LABELS.get(metric)
    if labels is None or raw is None:
        return raw
    if isinstance(raw, str):
        key = raw.strip().lower()
        alias = _ALIASES.get(metric, {}).get(key)
        if alias:
            return alias
        snake = _snake(raw)
        if snake in labels:
            return snake
        for ident, label in labels.items():
            if label.lower() == key:
                return ident
        try:
            raw = float(raw)
        except ValueError:
            return raw
    if metric == "grid_status":
        return _der_mode(int(raw))
    codes = _DEFAULT_CODES.get(metric)
    if codes is not None:
        return codes.get(int(raw), f"unknown_{int(raw)}")
    return raw


def _der_mode(v: int) -> str:
    """701.DERMode bitfield, in franklinwh-modbus-bridge's priority order (bit2, bit1, bit0)."""
    if v & 4:
        return "pv_clipped"
    if v & 2:
        return "grid_forming"
    return "grid_following"   # bit0 set, or none set (the default)


def convert(mapping: Any, raw: Any, scale: float | None = None) -> Any:
    """One raw source value → internal value, for any protocol.

    ``mapping`` is a :class:`FieldMapping`. ``scale`` overrides ``mapping.scale``
    (Modbus carries its scale inside the source string).
    """
    if raw is None:
        return None
    metric = mapping.target_metric
    value_map = getattr(mapping, "value_map", None) or {}
    if value_map:
        key = str(raw).strip()
        # "1" and 1.0 both match a "1" key
        try:
            num = float(key)
            key_num = str(int(num)) if num.is_integer() else key
        except ValueError:
            key_num = key
        for candidate in (key, key_num, key.lower()):
            if candidate in value_map:
                return value_map[candidate]
    if metric in STATE_LABELS:
        return state_id(metric, raw)
    factor = mapping.scale if scale is None else scale
    try:
        value = float(raw) * factor
    except (TypeError, ValueError):
        return raw
    signed = SIGNED_METRICS.get(metric)
    positive = getattr(mapping, "positive", None)
    if signed and positive and positive != signed[0]:
        value = -value
    return value


def is_unmapped(metric: str, value: Any) -> bool:
    """A state metric whose value has no known meaning (e.g. a bare ``-3``)."""
    labels = STATE_LABELS.get(metric)
    return labels is not None and value is not None and not (isinstance(value, str) and value in labels)


def split_unmapped(points: dict[str, Any]) -> dict[str, Any]:
    """Remove state values with no known meaning from ``points`` and return them (#30).

    They are never shown or stored as a state; the integration reports them so the
    user can add a value map.
    """
    bad = {k: v for k, v in points.items() if is_unmapped(k, v)}
    for k in bad:
        del points[k]
    return bad


def derive(points: dict[str, Any]) -> dict[str, Any]:
    """Fill what a source left out, from internal values. Returns ``points``."""
    pw = points.get("battery_power_w")
    if "battery_state" not in points and isinstance(pw, (int, float)):
        points["battery_state"] = (
            "discharging" if pw > STANDBY_BAND_W
            else "charging" if pw < -STANDBY_BAND_W else "standby")
    return points


def state_code(metric: str, value: Any) -> int | None:
    """Stored numeric code for an internal state id (1-based), or None."""
    labels = STATE_LABELS.get(metric)
    if labels is None or not isinstance(value, str):
        return None
    try:
        return list(labels).index(value) + 1
    except ValueError:
        return None
