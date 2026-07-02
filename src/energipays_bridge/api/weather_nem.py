"""Weather (Open-Meteo) + Australian NEM spot-price overlay."""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["weather-nem"])

# ---------------------------------------------------------------------------
# In-memory cache  {cache_key: (timestamp, data)}
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}
_TTL = 300  # seconds

_WMO_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    56: "Drizzle",
    57: "Drizzle",
    61: "Rain",
    63: "Rain",
    65: "Rain",
    66: "Rain",
    67: "Rain",
    71: "Snow",
    73: "Snow",
    75: "Snow",
    77: "Snow",
    80: "Showers",
    81: "Showers",
    82: "Showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}

def _wmo_description(code: int) -> str:
    if code in _WMO_DESCRIPTIONS:
        return _WMO_DESCRIPTIONS[code]
    if code <= 2:
        return "Mainly clear"
    if code <= 48:
        return "Fog"
    if code <= 57:
        return "Drizzle"
    if code <= 67:
        return "Rain"
    if code <= 77:
        return "Snow"
    if code <= 82:
        return "Showers"
    return "Thunderstorm"

def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _TTL:
        return entry[1]
    return None

def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


async def _fetch_weather(lat: float, lon: float) -> dict | None:
    cache_key = f"weather:{lat:.4f}:{lon:.4f}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weathercode&timezone=auto"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        current = data.get("current", {})
        code = int(current.get("weathercode", 0))
        result = {
            "temp_c": round(float(current.get("temperature_2m", 0)), 1),
            "code": code,
            "description": _wmo_description(code),
        }
        _cache_set(cache_key, result)
        return result
    except Exception:
        return None


async def _fetch_nem(region: str) -> dict | None:
    cache_key = f"nem:{region}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        url = "https://visualisations.aemo.com.au/aemo/apps/api/report/5MIN"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"timeScale": ["5MIN"]})
            resp.raise_for_status()
            data = resp.json()
        rows = data.get("5MIN", [])
        matching = [r for r in rows if r.get("REGIONID") == region]
        if not matching:
            return None
        row = matching[-1]
        rrp = float(row.get("RRP", 0))
        result = {
            "rrp_mwh": round(rrp, 2),
            "rrp_kwh": round(rrp / 1000, 6),
            "region": region,
        }
        _cache_set(cache_key, result)
        return result
    except Exception:
        return None


@router.get("/api/weather-nem")
async def get_weather_nem(request: Request):
    db = request.app.state.db
    from ..store.db import get_config  # noqa: PLC0415

    # Prefer saved setting → device GPS → Sydney default
    pts = getattr(request.app.state, "latest_points", {})
    device_lat = pts.get("dev.latitude")
    device_lon = pts.get("dev.longitude")
    default_lat = device_lat if device_lat else -33.8688
    default_lon = device_lon if device_lon else 151.2093
    lat = float(await get_config(db, "weather_lat", default_lat))
    lon = float(await get_config(db, "weather_lon", default_lon))
    region = await get_config(db, "weather_region", "NSW1")

    weather = await _fetch_weather(lat, lon)
    nem = await _fetch_nem(region)

    return {
        "weather": weather,
        "nem": nem,
        "settings": {"lat": lat, "lon": lon, "region": region},
    }


class NemSettings(BaseModel):
    lat: float
    lon: float
    region: str


@router.put("/api/weather-nem/settings")
async def put_weather_nem_settings(body: NemSettings, request: Request):
    db = request.app.state.db
    from ..store.db import set_config  # noqa: PLC0415

    await set_config(db, "weather_lat", body.lat)
    await set_config(db, "weather_lon", body.lon)
    await set_config(db, "weather_region", body.region)
    return {"ok": True}
