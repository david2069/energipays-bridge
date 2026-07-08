"""Solar PV production forecast via Open-Meteo (free, no API key).

Returns estimated AC kW output for the configured system (size/tilt/azimuth),
not raw horizontal irradiance — see BACKLOG.md Package 2a for the scaling
bug this replaces.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["solar"])

# Performance ratio: fixed, rough real-world derating for inverter/wiring/soiling
# losses on top of ideal panel output. Not vendor-specific; a reasonable default.
_PERFORMANCE_RATIO = 0.85

# Cache holds the raw GTI/temp series only (depends on lat/lon/tilt/azimuth) —
# kWp scaling is applied on every read, so changing just the system size never
# needs a re-fetch.
_cache: dict[str, tuple[float, Any]] = {}
_TTL = 3600  # 1 hour — radiation forecast doesn't change minute-to-minute


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


async def _get_solar_config(db) -> dict:
    from ..store.db import get_config  # noqa: PLC0415

    kwp_raw = await get_config(db, "solar_kwp", "")
    return {
        "kwp": float(kwp_raw) if kwp_raw else None,
        "tilt_deg": float(await get_config(db, "solar_tilt_deg", "22")),
        "azimuth_deg": float(await get_config(db, "solar_azimuth_deg", "180")),
    }


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
    solar_cfg = await _get_solar_config(db)
    tilt = solar_cfg["tilt_deg"]
    azimuth = solar_cfg["azimuth_deg"]
    kwp = solar_cfg["kwp"]

    cache_key = f"solar:{lat:.4f}:{lon:.4f}:{tilt:.1f}:{azimuth:.1f}"
    cached = _cache_get(cache_key)
    if cached is None:
        try:
            from datetime import date, timedelta
            today = date.today().isoformat()
            end   = (date.today() + timedelta(days=6)).isoformat()
            url = (
                f"https://historical-forecast-api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&hourly=global_tilted_irradiance,temperature_2m"
                f"&tilt={tilt}&azimuth={azimuth}"
                f"&start_date={today}&end_date={end}&timezone=auto"
            )
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                om = resp.json()

            temps = om["hourly"].get("temperature_2m", [None] * len(om["hourly"]["time"]))
            raw_hourly = [
                {"hour": h, "gti_wm2": round(float(v), 1),
                 "temp_c": round(float(t), 1) if t is not None else None}
                for h, v, t in zip(
                    om["hourly"]["time"],
                    om["hourly"]["global_tilted_irradiance"],
                    temps,
                )
            ]
            cached = {"hourly": raw_hourly}
            _cache_set(cache_key, cached)
        except Exception as exc:
            return {"hourly": [], "error": str(exc), "lat": lat, "lon": lon,
                     "kwp": kwp, "tilt_deg": tilt, "azimuth_deg": azimuth}

    # Apply kWp scaling on every read — cheap, and means changing just the
    # system size never needs a re-fetch from Open-Meteo.
    hourly = [
        {
            **h,
            "estimated_kw": round(h["gti_wm2"] / 1000 * kwp * _PERFORMANCE_RATIO, 3) if kwp else None,
        }
        for h in cached["hourly"]
    ]
    return {"hourly": hourly, "lat": lat, "lon": lon,
            "kwp": kwp, "tilt_deg": tilt, "azimuth_deg": azimuth}


class SolarSettingsIn(BaseModel):
    kwp: float | None = None
    tilt_deg: float = 22
    azimuth_deg: float = 180


@router.get("/api/solar/settings")
async def get_solar_settings(request: Request) -> dict:
    return await _get_solar_config(request.app.state.db)


@router.put("/api/solar/settings")
async def put_solar_settings(body: SolarSettingsIn, request: Request) -> dict:
    from ..store.db import set_config  # noqa: PLC0415

    db = request.app.state.db
    await set_config(db, "solar_kwp", str(body.kwp) if body.kwp else "")
    await set_config(db, "solar_tilt_deg", str(body.tilt_deg))
    await set_config(db, "solar_azimuth_deg", str(body.azimuth_deg))
    return {"ok": True}
