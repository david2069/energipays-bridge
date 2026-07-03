"""Push notification dispatcher — sends to all enabled HA companion devices."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ── Notification templates ────────────────────────────────────────────────────

_TEMPLATES: dict[str, tuple[str, str]] = {
    "device_online":    ("Sunamp Online",           "Device connected ({ts})"),
    "device_offline":   ("Sunamp Offline",          "Device went offline ({ts})"),
    "boost_started":    ("Boost Started",            "Boost is now active"),
    "boost_ended":      ("Boost Ended",              "Boost completed"),
    "offpeak_started":  ("Off-Peak Active",          "Off-peak heating rule started"),
    "offpeak_ended":    ("Off-Peak Ended",           "Off-peak heating rule stopped"),
    "temp_threshold":   ("Temperature Alert",        "Water temp {temp}°C (T1:{t1} T2:{t2} T3:{t3}) reached threshold {threshold}°C"),
    "done_heating":     ("Heating Done",             "Water temperature reached setpoint ({temp}°C)"),
    "test":             ("Energipays Bridge Test",   "Notification routing is working ✓"),
}


def _build_payload(event_type: str, context: dict) -> dict:
    title_tmpl, msg_tmpl = _TEMPLATES.get(
        event_type, ("Energipays Alert", "Event: " + event_type)
    )
    try:
        title = title_tmpl.format(**context)
        message = msg_tmpl.format(**context)
    except KeyError:
        title = title_tmpl
        message = msg_tmpl
    return {"title": title, "message": message, "data": {}}


async def _post_to_device(device: dict, payload: dict) -> dict:
    host = device["instance_host"].rstrip("/")
    service = device["service_target"]
    token = device["instance_token"]
    url = f"{host}/api/services/notify/{service}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        ok = r.status_code in (200, 201)
        if not ok:
            log.warning("Notify %s → %d: %s", service, r.status_code, r.text[:200])
        return {"device": device["alias"], "ok": ok, "status": r.status_code}
    except Exception as exc:
        log.warning("Notify %s error: %s", service, exc)
        return {"device": device["alias"], "ok": False, "error": str(exc)}


async def send_notification(
    db,
    event_type: str,
    context: dict[str, Any] | None = None,
) -> dict:
    """Send a push notification to all enabled companion devices.

    Returns a summary dict: {"sent": bool, "reason": str, "results": list}.
    """
    from ..store.db import get_notification_settings, get_notification_devices

    context = context or {}
    settings = await get_notification_settings(db)

    if not settings.get("enabled"):
        return {"sent": False, "reason": "disabled", "results": []}
    if event_type != "test" and event_type not in settings.get("triggers", []):
        return {"sent": False, "reason": "trigger_off", "results": []}

    all_devices = await get_notification_devices(db)
    devices = [
        d for d in all_devices
        if d["enabled"] and d.get("instance_enabled", 1)
    ]
    if not devices:
        return {"sent": False, "reason": "no_devices", "results": []}

    payload = _build_payload(event_type, context)
    results = await asyncio.gather(
        *[_post_to_device(dev, payload) for dev in devices],
        return_exceptions=True,
    )
    norm_results = [
        r if isinstance(r, dict) else {"ok": False, "error": str(r)}
        for r in results
    ]
    sent = any(r.get("ok") for r in norm_results)
    log.info("Notification %s → sent=%s (%d devices)", event_type, sent, len(devices))

    # Persist to notification_log for UI history
    try:
        import json, time
        device_aliases = [d["alias"] for d in devices]
        await db.execute(
            "INSERT INTO notification_log (event_type, ts, context, devices, ok) VALUES (?,?,?,?,?)",
            (event_type, time.time(), json.dumps(context), json.dumps(device_aliases), int(sent)),
        )
        await db.commit()
    except Exception as exc:
        log.warning("notification_log write failed: %s", exc)

    return {"sent": sent, "reason": "ok" if sent else "all_failed", "results": norm_results}
