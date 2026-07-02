from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/points/latest")
async def latest_points(request: Request) -> dict:
    """Return the most recent polled sample as a flat key→value dict."""
    needs_setup = not getattr(request.app.state, "device_id", None)
    pts = dict(request.app.state.latest_points)
    # Merge ext.* keys from integration registry
    reg = getattr(request.app.state, "integration_registry", None)
    if reg is not None:
        pts.update(reg.latest)
    return {
        "points": pts,
        "ts": request.app.state.latest_ts,
        "quality": request.app.state.latest_quality,
        "connected": request.app.state.poller.connected if request.app.state.poller else False,
        "last_poll_ts": request.app.state.poller.last_poll_ts if request.app.state.poller else 0,
        "last_error": request.app.state.poller.last_error if request.app.state.poller else "",
        "needs_setup": needs_setup,
        "device_id": getattr(request.app.state, "device_id", ""),
        "safe_mode": getattr(request.app.state, "safe_mode", True),
        # User identity + device location extracted from device profile
        "user_email": pts.get("user.email", ""),
        "user_name": pts.get("user.name", ""),
        "device_name": pts.get("dev.name", ""),
        "device_timezone": pts.get("dev.timezone", ""),
        "device_lat": pts.get("dev.latitude"),
        "device_lon": pts.get("dev.longitude"),
        "user_address": pts.get("user.address", ""),
        "user_city": pts.get("user.city", ""),
        "user_state": pts.get("user.state", ""),
        "user_country": pts.get("user.country", ""),
    }
