from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..boost import PERIOD_LABEL, VALID_PERIODS
from ..cloud_result import label, outcome

log = logging.getLogger("energipays_bridge.api.devices")
router = APIRouter()


@router.get("/api/devices")
async def list_devices(request: Request) -> dict:
    client = _get_client(request)
    return await _thread(client.devices)


@router.get("/api/device/status")
async def device_status(request: Request) -> dict:
    client = _get_client(request)
    device_id = request.app.state.device_id
    return await _thread(client.device_status, [device_id])


@router.get("/api/device/profile")
async def device_profile(request: Request) -> dict:
    """Full device object from GET /api/devices/{id} — includes status_data, device_statuses, telemetryData."""
    client = _get_client(request)
    device_id = request.app.state.device_id
    return await _thread(client.device, device_id)


class DeviceStatusBody(BaseModel):
    fields: dict[str, int] = {}
    rule_id: str | None = None   # UUID to activate, or "0" to clear
    rule_type: str = "command"   # "command" (PD), "offpeak" (Off-Peak), "heater2" (Heater 2)


_FIELD_LABELS = {
    "status":       "Power Diverter",
    "heaterStatus": "Immersion Heater",
    "offpeakSwitcherStatus":          "Off-Peak Switcher",
    "weatherSwitcherStatus":          "Weather Boost Switcher",
    "solarRestrictionSwitcherStatus": "Solar Restriction",
    "priorityModeSwitcherStatus":     "Priority Mode",
}


@router.post("/api/device/set")
async def set_device_status(body: DeviceStatusBody, request: Request) -> dict:
    client = _get_client(request)
    _check_writable(request, f"POST /api/device/set {body.fields or {'rule_id': body.rule_id}}")
    device_id = request.app.state.device_id
    if body.rule_id is not None:
        # Same body as energipays.com: the device's current status_data with only
        # this circuit's rule field changed (energipays-client activate_rule, #2).
        rule_type = _rule_type(body.rule_type)
        result = await _thread(client.activate_rule, device_id, body.rule_id, rule_type)
        circuit = {"command": "PD", "offpeak": "Off-Peak", "heater2": "Heater 2"}.get(rule_type, rule_type)
        action = "cleared" if body.rule_id == "0" else f"→ {body.rule_id}"
        description = f"{circuit} rule {action}"
    elif "boost_power" in body.fields:
        # Boost power uses a separate endpoint: POST /api/devices/{id}/boostPower
        idx = body.fields["boost_power"]
        result = await _thread(client.set_boost_power, device_id, idx)
        description = f"Boost Power → {idx} ({idx * 25}%)"
    else:
        result = await _thread(client.set_device_status, device_id, **body.fields)
        parts = [f"{_FIELD_LABELS.get(field, field)} → {'ON' if val == 1 else 'OFF'}"
                 for field, val in body.fields.items()]
        description = ", ".join(parts) if parts else "device status"
    # Log AFTER checking the result — the previous version logged "OK" here
    # unconditionally, before the error check below, so a rejected command
    # was misreported in the log as having succeeded.
    _raise_unless_ok("cmd (UI)", description, result)
    return result


@router.get("/api/rules")
async def list_rules(request: Request) -> dict:
    client = _get_client(request)
    return await _thread(client.rules)


class CreateRuleBody(BaseModel):
    name: str
    type: str = "command"   # "command" (PD), "offpeak", "heater2"


@router.post("/api/rules")
async def create_rule(body: CreateRuleBody, request: Request) -> dict:
    import logging as _log
    client = _get_client(request)
    rule_type = _rule_type(body.type)
    _check_writable(request, f"POST /api/rules name={body.name!r} type={rule_type!r}")
    result = await _thread(client.create_rule, body.name, rule_type)
    _raise_unless_ok("rules", f"create rule '{body.name}' (type={rule_type})", result)
    return result


class RuleBody(BaseModel):
    rule: dict


@router.put("/api/rules/{rule_id}")
async def update_rule(rule_id: str, body: RuleBody, request: Request) -> dict:
    import logging as _log
    from fastapi import HTTPException
    client = _get_client(request)
    _check_writable(request, f"PUT /api/rules/{rule_id} {body.rule}")
    result = await _thread(client.update_rule, rule_id, body.rule)
    # _handle() returns {"error": ..., "status": N} on cloud HTTP errors (doesn't raise)
    if isinstance(result, dict) and "error" in result:
        status = result.get("status", 502)
        detail = result.get("error_decrypted") or result.get("error") or "Cloud API error"
        _log.getLogger(__name__).error(
            "rules: cloud rejected update for rule %s — HTTP %s: %s", rule_id, status, detail
        )
        raise HTTPException(status_code=status if 400 <= status < 600 else 502, detail=str(detail))
    _log.getLogger(__name__).info(
        "rules: rule %s (%s) updated successfully", rule_id, body.rule.get("name", "")
    )
    return result


class RuleNameBody(BaseModel):
    name: str


@router.put("/api/rules/{rule_id}/name")
async def rename_rule(rule_id: str, body: RuleNameBody, request: Request) -> dict:
    import logging as _log
    from fastapi import HTTPException
    client = _get_client(request)
    _check_writable(request, f"PUT /api/rules/{rule_id}/name name={body.name!r}")
    result = await _thread(client.rename_rule, rule_id, body.name)
    if isinstance(result, dict) and "error" in result:
        status = result.get("status", 502)
        detail = result.get("error_decrypted") or result.get("error") or "Cloud API error"
        _log.getLogger(__name__).error(
            "rules: cloud rejected rename for rule %s — HTTP %s: %s", rule_id, status, detail
        )
        raise HTTPException(status_code=status if 400 <= status < 600 else 502, detail=str(detail))
    _log.getLogger(__name__).info("rules: rule %s renamed to %s", rule_id, body.name)
    return result


@router.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request) -> dict:
    import logging as _log
    client = _get_client(request)
    _check_writable(request, f"DELETE /api/rules/{rule_id}")
    result = await _thread(client.delete_rule, rule_id)
    _raise_unless_ok("rules", f"delete rule {rule_id}", result)
    return result


class DeviceSwitchBody(BaseModel):
    device_id: str


@router.get("/api/device/list")
async def list_devices_v2(request: Request) -> dict:
    client = _get_client(request)
    return await _thread(client.devices)


@router.post("/api/device/switch")
async def switch_device(body: DeviceSwitchBody, request: Request) -> dict:
    import logging as _log
    client = _get_client(request)
    _check_writable(request, f"POST /api/device/switch device_id={body.device_id!r}")

    raw = await _thread(client.devices)
    devices: list = raw.get("data", raw) if isinstance(raw, dict) else raw

    dev = next((d for d in devices if d.get("id") == body.device_id), None)
    if dev is None:
        raise HTTPException(404, f"Device '{body.device_id}' not found")

    current_data_server = request.app.state.data_server

    if request.app.state.poller is not None:
        await request.app.state.poller.stop()

    request.app.state.device_id = body.device_id
    request.app.state.data_server = dev.get("server", current_data_server)
    # MQTT commands must follow the switch too (#8): before this, HA kept
    # commanding the previous device.
    request.app.state._mqtt_device_id = body.device_id
    request.app.state._mqtt_data_server = request.app.state.data_server
    pub = getattr(request.app.state, "mqtt_publisher", None)
    if pub is not None:
        pub.set_device(body.device_id, request.app.state.data_server)

    from ..poller import EnergipaysPoller
    settings = request.app.state.settings
    new_poller = EnergipaysPoller(
        client,
        request.app.state.bus,
        body.device_id,
        request.app.state.data_server,
        poll_interval=settings.poll_interval,
        tz=getattr(settings, "timezone", "Australia/Sydney"),
    )
    await new_poller.start()
    request.app.state.poller = new_poller

    _log.getLogger(__name__).info(
        "device: switched to %s (%s)", body.device_id, dev.get("name", "")
    )
    return {"ok": True, "device_id": body.device_id, "device_name": dev.get("name", "")}


class BoostBody(BaseModel):
    period: int = 2          # 2=1h, 3=2h, 4=3h (as energipays.com sends; 1 cancels)
    clear_rule: bool = False  # clear the active PD rule first (Energipays now
                               # refuses to boost while a rule is active — the
                               # cloud reports this as a misleading generic
                               # "Heater is disabled" error rather than naming
                               # the actual conflict)


@router.post("/api/boost")
async def boost(body: BoostBody, request: Request):
    if body.period not in VALID_PERIODS:
        raise HTTPException(
            400, "period must be 2 (1 h), 3 (2 h) or 4 (3 h); "
                 "period 1 is what energipays.com sends to cancel — use /api/boost/cancel")
    client = _get_client(request)
    _check_writable(request, f"POST /api/boost period={body.period} clear_rule={body.clear_rule}")
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server

    if body.clear_rule:
        # Note: this LEAVES the rule cleared — Energipays has no "temporary
        # override, restore after" concept, so the user must re-enable it
        # manually from Rules once the boost is done.
        clear_result = await _thread(client.activate_rule, device_id, "0", "command")
        _raise_unless_ok("cmd (UI)", "PD rule cleared (pre-boost)", clear_result)

    result = await _thread(client.boost_device, device_id, data_server, period=body.period)
    _raise_unless_ok("cmd (UI)", f"Boost → {PERIOD_LABEL[body.period]}", result)
    # Data server returns [] on success; normalise so callers always get a dict
    if isinstance(result, list):
        return {"ok": True, "period": body.period}
    return result


@router.post("/api/boost/cancel")
async def cancel_boost(request: Request):
    client = _get_client(request)
    _check_writable(request, "POST /api/boost/cancel")
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server
    result = await _thread(client.cancel_boost, device_id, data_server)
    _raise_unless_ok("cmd (UI)", "Boost cancel", result)
    if isinstance(result, list):
        return {"ok": True}
    return result


# ── helpers ──────────────────────────────────────────────────────────────────

import asyncio


async def _thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


_RULE_TYPES = ("command", "offpeak", "heater2")


def _rule_type(value: str) -> str:
    """Rule circuit as energipays.com spells it; ``off_peak`` was the bridge's typo (#6)."""
    v = "offpeak" if value == "off_peak" else value
    if v not in _RULE_TYPES:
        raise HTTPException(400, f"rule type must be one of {', '.join(_RULE_TYPES)}")
    return v


def _raise_unless_ok(source: str, description: str, result: object) -> None:
    """Log the real outcome of a cloud call and turn failures into HTTP errors (#8)."""
    kind, detail = outcome(result)
    msg = f"{source}: {description}: {label(kind, detail)}"
    if kind == "error":
        log.error(msg)
        raise HTTPException(502, f"{description} failed: {detail}")
    if kind == "skipped":
        log.warning(msg)
        raise HTTPException(409, f"{description} not sent: {detail}")
    log.info(msg)


def _get_client(request: Request):
    """Return the EnergipaysClient, or 503 if the bridge hasn't finished connecting yet.

    Fresh installs (pre-credentials) and the window between the setup wizard's
    "Save & connect" and poller-init completing both leave app.state.client as
    None; without this guard those routes crash with AttributeError -> 500,
    which the Rules tab (and others) render as a misleading empty state.
    """
    client = request.app.state.client
    if client is None:
        raise HTTPException(503, "bridge not connected yet")
    return client


def _check_writable(request: Request, description: str) -> None:
    """Block device-modifying commands when READ_ONLY is set.

    Logs the exact write that would have been sent — useful for dry-run
    debugging of this reverse-engineered API — then refuses with 403.
    """
    if request.app.state.settings.read_only:
        log.warning("READ_ONLY: blocked %s", description)
        raise HTTPException(403, f"Bridge is read-only (READ_ONLY=true) — blocked: {description}")
