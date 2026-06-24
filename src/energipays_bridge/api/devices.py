from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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


class DeviceStatusBody(BaseModel):
    fields: dict[str, int]   # e.g. {"status": 1} or {"heaterStatus": 0}


@router.post("/api/device/set")
async def set_device_status(body: DeviceStatusBody, request: Request) -> dict:
    _require_writes(request)
    client = request.app.state.client
    device_id = request.app.state.device_id
    return await _thread(client.set_device_status, device_id, **body.fields)


@router.get("/api/rules")
async def list_rules(request: Request) -> dict:
    client = request.app.state.client
    return await _thread(client.rules)


class BoostBody(BaseModel):
    period: int = 1   # 1=1h, 2=2h, 3=3h


@router.post("/api/boost")
async def boost(body: BoostBody, request: Request) -> dict:
    _require_writes(request)
    if body.period not in (1, 2, 3):
        raise HTTPException(400, "period must be 1, 2, or 3")
    client = request.app.state.client
    device_id = request.app.state.device_id
    data_server = request.app.state.data_server
    return await _thread(client.boost_device, device_id, data_server, period=body.period)


# ── helpers ──────────────────────────────────────────────────────────────────

import asyncio


async def _thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _require_writes(request: Request) -> None:
    safe = request.app.state.safe_mode
    if safe:
        raise HTTPException(403, "Safe Mode is enabled — writes are disabled")
