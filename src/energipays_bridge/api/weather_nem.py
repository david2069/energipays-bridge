"""Weather (Open-Meteo) + Australian NEM spot-price overlay."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["weather-nem"])

# ---------------------------------------------------------------------------
# In-memory cache  {cache_key: (timestamp, data)}
# Last good value is kept past its TTL so it can be served stale while the
# upstream is failing (negative-cached for _FAIL_TTL between attempts).
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}
_TTL = 300  # seconds
_FAIL_TTL = 120  # seconds between retries after an upstream failure
_fail: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Shared AsyncClient — reuses connections instead of a TLS handshake per call."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10)
    return _client


async def _cached_fetch(key: str, fetch) -> Any | None:
    """Single-flight cached fetch.

    Concurrent callers on a cache miss share one upstream request (burst-safe);
    failures are negative-cached and the last good value is served stale.
    """
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _TTL:
        return entry[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        entry = _cache.get(key)  # re-check: the lock holder may have refreshed
        if entry and (time.time() - entry[0]) < _TTL:
            return entry[1]
        if (time.time() - _fail.get(key, 0)) < _FAIL_TTL:
            return entry[1] if entry else None
        try:
            value = await fetch(_get_client())
            _cache[key] = (time.time(), value)
            _fail.pop(key, None)
            return value
        except Exception as exc:
            _fail[key] = time.time()
            log.warning("weather-nem: %s fetch failed — %s: %s (%s)", key,
                        type(exc).__name__, exc,
                        "serving stale" if entry else "no stale value")
            return entry[1] if entry else None

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

async def _fetch_weather(lat: float, lon: float) -> dict | None:
    async def go(client: httpx.AsyncClient) -> dict:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weathercode&timezone=auto"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        current = resp.json().get("current", {})
        code = int(current.get("weathercode", 0))
        return {
            "temp_c": round(float(current.get("temperature_2m", 0)), 1),
            "code": code,
            "description": _wmo_description(code),
        }
    return await _cached_fetch(f"weather:{lat:.4f}:{lon:.4f}", go)


async def _fetch_nem(region: str) -> dict | None:
    async def go(client: httpx.AsyncClient) -> dict:
        # ELEC_NEM_SUMMARY is ~12 KB with a PRICE per region — the 5MIN report
        # is ~700 KB per call, which made concurrent fetches time out their TLS
        # handshakes inside the Docker NAT.
        url = "https://visualisations.aemo.com.au/aemo/apps/api/report/ELEC_NEM_SUMMARY"
        resp = await client.post(url, json={})
        resp.raise_for_status()
        rows = resp.json().get("ELEC_NEM_SUMMARY", [])
        matching = [r for r in rows if r.get("REGIONID") == region]
        if not matching:
            raise RuntimeError(f"region {region} not present in NEM summary")
        rrp = float(matching[-1].get("PRICE", 0))
        return {
            "rrp_mwh": round(rrp, 2),
            "rrp_kwh": round(rrp / 1000, 6),
            "region": region,
        }
    return await _cached_fetch(f"nem:{region}", go)


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
