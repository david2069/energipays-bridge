from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger("energipays_bridge.api.cloud_stats")
router = APIRouter()


def _thread(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


@router.get("/api/cloud/stats")
async def cloud_stats(
    request: Request,
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str   = Query(..., description="YYYY-MM-DD"),
    data_type: str = Query("power", description="power or energy"),
    phase: str     = Query("sum"),
) -> dict:
    """Proxy energipays data-server stats() — returns per-interval chart data for any date range."""
    client = request.app.state.client
    if client is None:
        raise HTTPException(503, "Client not initialised")
    device_id   = request.app.state.device_id
    data_server = request.app.state.data_server
    settings    = request.app.state.settings
    tz = getattr(settings, "timezone", "Australia/Sydney")
    try:
        raw = await _thread(client.stats, device_id, date_from, date_to,
                            data_server, tz, data_type, phase)
        return {"date_from": date_from, "date_to": date_to,
                "data_type": data_type, "phase": phase, "raw": raw}
    except Exception as exc:
        log.error("cloud_stats error: %s", exc)
        raise HTTPException(502, f"Cloud API error: {exc}") from exc
