from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger("energipays_bridge.api.devices")
router = APIRouter()


@router.get("/api/devices")
async def list_devices(request: Request) -> dict:
    client = request.app.state.client
    return await _thread(client.devices)


@router.get("/api/device/status")
async def device_status(request: Request) -> dict:
    client = request.app.state.client
    device_id = request.app.state.device_id
    return await _thread(client.device_status, [device_id])


@router.get("/api/device/profile")
async def device_profile(request: Request) -> dict:
    """Full device object from GET /api/devices/{id} — includes status_data, device_statuses, telemetryData."""
    client = request.app.state.client
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
    _require_writes(request)
    client = request.app.state.client
    device_id = request.app.state.device_id
    if body.rule_id is not None:
        # Quick-set or clear active rule for a circuit (no status_data needed)
        result = await _thread(client.set_rule, device_id, body.rule_id, body.rule_type)
        circuit = {"command": "PD", "offpeak": "Off-Peak", "heater2": "Heater 2"}.get(body.rule_type, body.rule_type)
        action = "cleared" if body.rule_id == "0" else f"→ {body.rule_id}"
        log.info("cmd (UI): %s rule %s", circuit, action)
    elif "boost_power" in body.fields:
        # Boost power uses a separate endpoint: POST /api/devices/{id}/boostPower
        idx = body.fields["boost_power"]
        result = await _thread(client.set_boost_power, device_id, idx)
        log.info("cmd (UI): Boost Power → %d (%d%%)", idx, idx * 25)
    else:
        result = await _thread(client.set_device_status, device_id, **body.fields)
        for field, val in body.fields.items():
            label = _FIELD_LABELS.get(field, field)
            state = "ON" if val == 1 else "OFF"
            log.info("cmd (UI): %s → %s", label, state)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(502, f"Command failed: {result.get('body') or result['error']}")
    return result


@router.get("/api/rules")
async def list_rules(request: Request) -> dict:
    client = request.app.state.client
    return await _thread(client.rules)


class CreateRuleBody(BaseModel):
    name: str
    type: str = "command"   # "command" (PD), "off_peak", "heater2"


@router.post("/api/rules")
async def create_rule(body: CreateRuleBody, request: Request) -> dict:
    import logging as _log
    _require_writes(request)
    client = request.app.state.client
    result = await _thread(client.create_rule, body.name, body.type)
    _log.getLogger(__name__).info("rules: created rule '%s' (type=%s)", body.name, body.type)
    return result


class RuleBody(BaseModel):
    rule: dict


@router.put("/api/rules/{rule_id}")
async def update_rule(rule_id: str, body: RuleBody, request: Request) -> dict:
    import logging as _log
    from fastapi import HTTPException
    _require_writes(request)
    client = request.app.state.client
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


@router.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request) -> dict:
    import logging as _log
    _require_writes(request)
    client = request.app.state.client
    result = await _thread(client.delete_rule, rule_id)
    _log.getLogger(__name__).info("rules: deleted rule %s", rule_id)
    return result


class DeviceSwitchBody(BaseModel):
    device_id: str


@router.get("/api/device/list")
async def list_devices_v2(request: Request) -> dict:
    client = request.app.state.client
    return await _thread(client.devices)


@router.post("/api/device/switch")
async def switch_device(body: DeviceSwitchBody, request: Request) -> dict:
    import logging as _log
    _require_writes(request)
    client = request.app.state.client

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

    from ..poller import EnergipaysPoller
    settings = request.app.state.settings
    new_poller = EnergipaysPoller(
        client,
        request.app.state.bus,
        body.device_id,
        request.app.state.data_server,
        poll_interval=settings.poll_interval,
    )
    await new_poller.start()
    request.app.state.poller = new_poller

    _log.getLogger(__name__).info(
        "device: switched to %s (%s)", body.device_id, dev.get("name", "")
    )
    return {"ok": True, "device_id": body.device_id, "device_name": dev.get("name", "")}


class BoostBody(BaseModel):
    period: int = 1   # 1=1h, 2=2h, 3=3h


@router.post("/api/boost")
async def boost(body: BoostBody, request: Request):
    _require_writes(request)
    if body.period not in (1, 2, 3):
        raise HTTPException(400, "period must be 1, 2, or 3")
    client = request.app.state.client
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server
    result = await _thread(client.boost_device, device_id, data_server, period=body.period)
    hours = {1: "30 min", 2: "1 hour", 3: "2 hours"}.get(body.period, f"{body.period}h")
    if isinstance(result, dict) and "error" in result:
        log.warning("cmd (UI): Boost → %s: FAILED (%s)", hours, result.get('body') or result['error'])
        raise HTTPException(502, f"Boost failed: {result.get('body') or result['error']}")
    log.info("cmd (UI): Boost → %s: OK", hours)
    # Data server returns [] on success; normalise so callers always get a dict
    if isinstance(result, list):
        return {"ok": True, "period": body.period}
    return result


@router.post("/api/boost/cancel")
async def cancel_boost(request: Request):
    _require_writes(request)
    client = request.app.state.client
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server
    result = await _thread(client.cancel_boost, device_id, data_server)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(502, f"Cancel boost failed: {result.get('body') or result['error']}")
    log.info("cmd (UI): Boost cancelled")
    if isinstance(result, list):
        return {"ok": True}
    return result


# ── helpers ──────────────────────────────────────────────────────────────────

import asyncio


async def _thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _require_writes(request: Request) -> None:
    safe = request.app.state.safe_mode
    if safe:
        raise HTTPException(403, "Safe Mode is enabled — writes are disabled")
