from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
        # Quick-set or clear active rule for a circuit (no status_data needed)
        result = await _thread(client.set_rule, device_id, body.rule_id, body.rule_type)
        circuit = {"command": "PD", "offpeak": "Off-Peak", "heater2": "Heater 2"}.get(body.rule_type, body.rule_type)
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
    if isinstance(result, dict) and "error" in result:
        log.error("cmd (UI): %s: FAILED (%s)", description, result.get('body') or result['error'])
        raise HTTPException(502, f"Command failed: {result.get('body') or result['error']}")
    log.info("cmd (UI): %s: OK", description)
    return result


@router.get("/api/rules")
async def list_rules(request: Request) -> dict:
    client = _get_client(request)
    return await _thread(client.rules)


class CreateRuleBody(BaseModel):
    name: str
    type: str = "command"   # "command" (PD), "off_peak", "heater2"


@router.post("/api/rules")
async def create_rule(body: CreateRuleBody, request: Request) -> dict:
    import logging as _log
    client = _get_client(request)
    _check_writable(request, f"POST /api/rules name={body.name!r} type={body.type!r}")
    result = await _thread(client.create_rule, body.name, body.type)
    _log.getLogger(__name__).info("rules: created rule '%s' (type=%s)", body.name, body.type)
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
    _log.getLogger(__name__).info("rules: deleted rule %s", rule_id)
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
    period: int = 1          # 1=1h, 2=2h, 3=3h
    clear_rule: bool = False  # clear the active PD rule first (Energipays now
                               # refuses to boost while a rule is active — the
                               # cloud reports this as a misleading generic
                               # "Heater is disabled" error rather than naming
                               # the actual conflict)


@router.post("/api/boost")
async def boost(body: BoostBody, request: Request):
    if body.period not in (1, 2, 3):
        raise HTTPException(400, "period must be 1, 2, or 3")
    client = _get_client(request)
    _check_writable(request, f"POST /api/boost period={body.period} clear_rule={body.clear_rule}")
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server

    if body.clear_rule:
        # Note: this LEAVES the rule cleared — Energipays has no "temporary
        # override, restore after" concept, so the user must re-enable it
        # manually from Rules once the boost is done.
        clear_result = await _thread(client.set_rule, device_id, "0", "command")
        if isinstance(clear_result, dict) and "error" in clear_result:
            log.error("cmd (UI): clear rule before boost: FAILED (%s)",
                      clear_result.get('body') or clear_result['error'])
            raise HTTPException(502, f"Could not clear the active rule: {clear_result.get('body') or clear_result['error']}")
        log.info("cmd (UI): PD rule cleared (pre-boost)")

    result = await _thread(client.boost_device, device_id, data_server, period=body.period)
    hours = {1: "30 min", 2: "1 hour", 3: "2 hours"}.get(body.period, f"{body.period}h")
    if isinstance(result, dict) and "error" in result:
        log.error("cmd (UI): Boost → %s: FAILED (%s)", hours, result.get('body') or result['error'])
        raise HTTPException(502, f"Boost failed: {result.get('body') or result['error']}")
    log.info("cmd (UI): Boost → %s: OK", hours)
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
    if isinstance(result, dict) and "error" in result:
        log.error("cmd (UI): Boost cancel: FAILED (%s)", result.get('body') or result['error'])
        raise HTTPException(502, f"Cancel boost failed: {result.get('body') or result['error']}")
    log.info("cmd (UI): Boost cancelled")
    if isinstance(result, list):
        return {"ok": True}
    return result


# ── helpers ──────────────────────────────────────────────────────────────────

import asyncio


async def _thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


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
