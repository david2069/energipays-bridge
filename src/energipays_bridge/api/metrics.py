from __future__ import annotations

import time

from fastapi import APIRouter, Query, Request

from ..store.metrics import query_metrics

router = APIRouter()

_RANGE_SECONDS = {
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "12h": 43200, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400,
}
_BUCKET_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@router.get("/api/metrics/history")
async def metrics_history(
    request: Request,
    point: str = Query(..., description="point_id, e.g. phasePower"),
    range: str = Query("24h", description="preset range: 1h,2h,4h,6h,12h,24h,7d,30d"),
    bucket: str = Query("5m", description="bucket size: 1m,5m,15m,1h"),
    from_ts: float = Query(None, alias="from", description="unix timestamp — overrides range"),
    to_ts: float = Query(None, alias="to", description="unix timestamp — overrides range"),
    device_id_override: str = Query(None, alias="device_id", description="override device_id (e.g. 'ext')"),
) -> dict:
    bucket_s = _BUCKET_SECONDS.get(bucket, 300)
    now = time.time()
    if from_ts is None or to_ts is None:
        range_s = _RANGE_SECONDS.get(range, 86400)
        to_ts = now
        from_ts = now - range_s
    device_id = device_id_override if device_id_override else request.app.state.device_id
    db = request.app.state.db

    rows = await query_metrics(db, device_id, point, from_ts, to_ts, bucket_s)
    return {
        "point": point,
        "range": range,
        "bucket": bucket,
        "from": from_ts,
        "to": to_ts,
        "data": [{"ts": ts, "value": val} for ts, val in rows],
    }
