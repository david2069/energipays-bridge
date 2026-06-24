from __future__ import annotations

import collections
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..store.db import get_config, set_config

router = APIRouter()
_log_buffer: collections.deque = collections.deque(maxlen=500)


class _BridgeLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_buffer.append({
            "ts": record.created,
            "level": record.levelname,
            "name": record.name,
            "msg": self.format(record),
        })


def install_log_handler() -> None:
    handler = _BridgeLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


@router.get("/api/logs")
async def get_logs(request: Request) -> dict:
    return {"logs": list(_log_buffer)}


@router.get("/api/health")
async def health(request: Request) -> dict:
    poller = request.app.state.poller
    return {
        "status": "ok",
        "poller": {
            "connected": poller.connected if poller else False,
            "polls_total": poller.polls_total if poller else 0,
            "last_poll_ts": poller.last_poll_ts if poller else 0,
            "last_error": poller.last_error if poller else "",
        },
        "safe_mode": request.app.state.safe_mode,
        "device_id": request.app.state.device_id,
    }


@router.get("/api/config/{key}")
async def get_cfg(key: str, request: Request) -> dict:
    val = await get_config(request.app.state.db, key)
    return {"key": key, "value": val}


class ConfigBody(BaseModel):
    value: Any


@router.put("/api/config/{key}")
async def put_cfg(key: str, body: ConfigBody, request: Request) -> dict:
    allowed = {"safe_mode", "poll_interval", "raw_age_days", "retention_days", "log_level"}
    if key not in allowed:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown config key '{key}'")
    val = str(body.value)
    await set_config(request.app.state.db, key, val)
    # Apply safe_mode immediately
    if key == "safe_mode":
        request.app.state.safe_mode = val == "1"
    return {"key": key, "value": val}
