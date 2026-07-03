"""Solar irradiance forecast via Open-Meteo (free, no API key)."""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Request

router = APIRouter(tags=["solar"])

_cache: dict[str, tuple[float, Any]] = {}
_TTL = 3600  # 1 hour — radiation forecast doesn't change minute-to-minute


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


@router.get("/api/solar/forecast")
async def solar_forecast(request: Request):
    db = request.app.state.db
    from ..store.db import get_config  # noqa: PLC0415

    pts = getattr(request.app.state, "latest_points", {})
    device_lat = pts.get("dev.latitude")
    device_lon = pts.get("dev.longitude")
    default_lat = device_lat if device_lat else -33.8688
    default_lon = device_lon if device_lon else 151.2093

    lat = float(await get_config(db, "weather_lat", default_lat))
    lon = float(await get_config(db, "weather_lon", default_lon))

    cache_key = f"solar:{lat:.4f}:{lon:.4f}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        from datetime import date, timedelta
        today = date.today().isoformat()
        end   = (date.today() + timedelta(days=6)).isoformat()
        url = (
            f"https://historical-forecast-api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=shortwave_radiation,temperature_2m"
            f"&start_date={today}&end_date={end}&timezone=auto"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            om = resp.json()

        temps = om["hourly"].get("temperature_2m", [None] * len(om["hourly"]["time"]))
        hourly = [
            {"hour": h, "radiation_wm2": round(float(v), 1),
             "temp_c": round(float(t), 1) if t is not None else None}
            for h, v, t in zip(
                om["hourly"]["time"],
                om["hourly"]["shortwave_radiation"],
                temps,
            )
        ]
        result = {"hourly": hourly, "lat": lat, "lon": lon}
        _cache_set(cache_key, result)
        return result

    except Exception as exc:
        return {"hourly": [], "error": str(exc), "lat": lat, "lon": lon}
