"""Push notification API — HA instances, companion devices, settings, test."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..store.db import (
    delete_ha_instance,
    delete_notification_device,
    get_ha_instance,
    get_ha_instances,
    get_notification_devices,
    get_notification_log,
    get_notification_log_stats,
    get_notification_settings,
    update_notification_settings,
    upsert_ha_instance,
    upsert_notification_device,
)

router = APIRouter(tags=["notifications"])
log = logging.getLogger(__name__)

_MASKED = "••••"


# ── Pydantic models ───────────────────────────────────────────────────────────

class HAInstanceIn(BaseModel):
    id: Optional[str] = None
    alias: str
    host: str
    token: str
    enabled: bool = True
    is_default: bool = False


class NotificationDeviceIn(BaseModel):
    id: Optional[str] = None
    ha_instance_id: str
    alias: str
    service_target: str
    enabled: bool = True


class NotificationSettingsIn(BaseModel):
    enabled: bool
    triggers: list[str]
    temp_threshold: float = 40.0


# ── HA Instances ──────────────────────────────────────────────────────────────

@router.get("/api/ha/instances")
async def list_ha_instances(request: Request) -> list[dict]:
    rows = await get_ha_instances(request.app.state.db)
    for r in rows:
        r["token"] = _MASKED
    return rows


@router.post("/api/ha/instances")
async def upsert_ha_instance_route(body: HAInstanceIn, request: Request) -> dict:
    db = request.app.state.db
    inst = body.model_dump()
    if not inst.get("id"):
        inst["id"] = str(uuid.uuid4())

    # Connectivity test before save (skip if token is masked — editing existing)
    if inst["token"] != _MASKED:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    inst["host"].rstrip("/") + "/api/",
                    headers={"Authorization": f"Bearer {inst['token']}"},
                )
            if r.status_code not in (200, 201):
                raise HTTPException(400, f"HA not reachable: HTTP {r.status_code}")
        except httpx.ConnectError as exc:
            raise HTTPException(400, f"Cannot connect to HA: {exc}")
        except httpx.TimeoutException:
            raise HTTPException(400, "Timeout connecting to HA")

    await upsert_ha_instance(db, inst)
    return {"ok": True, "id": inst["id"]}


@router.delete("/api/ha/instances/{instance_id}")
async def delete_ha_instance_route(instance_id: str, request: Request) -> dict:
    await delete_ha_instance(request.app.state.db, instance_id)
    return {"ok": True}


@router.get("/api/ha/instances/{instance_id}/targets")
async def get_ha_targets(instance_id: str, request: Request) -> list[dict]:
    """Return available notify.* services from a specific HA instance."""
    db = request.app.state.db
    inst = await get_ha_instance(db, instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                inst["host"].rstrip("/") + "/api/services",
                headers={"Authorization": f"Bearer {inst['token']}"},
            )
        r.raise_for_status()
        services = r.json()
    except Exception as exc:
        raise HTTPException(400, f"Failed to fetch HA services: {exc}")

    targets = []
    for svc in services:
        domain = svc.get("domain", "")
        if domain == "notify":
            for svc_name in svc.get("services", {}).keys():
                targets.append({
                    "service_target": svc_name,
                    "friendly_name": svc_name.replace("_", " ").title(),
                })
    return sorted(targets, key=lambda x: x["service_target"])


# ── Notification Devices ──────────────────────────────────────────────────────

@router.get("/api/ha/devices")
async def list_notification_devices(request: Request) -> list[dict]:
    rows = await get_notification_devices(request.app.state.db)
    for r in rows:
        r.pop("instance_token", None)
    return rows


@router.post("/api/ha/devices")
async def upsert_notification_device_route(
    body: NotificationDeviceIn, request: Request
) -> dict:
    dev = body.model_dump()
    if not dev.get("id"):
        dev["id"] = str(uuid.uuid4())
    await upsert_notification_device(request.app.state.db, dev)
    return {"ok": True, "id": dev["id"]}


@router.delete("/api/ha/devices/{device_id}")
async def delete_notification_device_route(device_id: str, request: Request) -> dict:
    await delete_notification_device(request.app.state.db, device_id)
    return {"ok": True}


# ── Notification Settings ─────────────────────────────────────────────────────

@router.get("/api/notification-settings")
async def get_notif_settings(request: Request) -> dict:
    return await get_notification_settings(request.app.state.db)


@router.put("/api/notification-settings")
async def put_notif_settings(body: NotificationSettingsIn, request: Request) -> dict:
    await update_notification_settings(request.app.state.db, body.model_dump())
    return {"ok": True}


# ── Test notification ─────────────────────────────────────────────────────────

@router.get("/api/notifications/log")
async def get_notif_log(
    request: Request,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    return await get_notification_log(request.app.state.db, event_type, limit)


@router.get("/api/notifications/stats")
async def get_notif_stats(request: Request) -> dict:
    return await get_notification_log_stats(request.app.state.db)


@router.post("/api/notifications/test")
async def test_notification(request: Request) -> dict:
    from ..notifications.dispatcher import send_notification
    result = await send_notification(request.app.state.db, "test")
    if not result["sent"] and result.get("reason") != "ok":
        # Return details even on failure so UI can show why
        return result
    return result
