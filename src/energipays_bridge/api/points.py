from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/points/latest")
async def latest_points(request: Request) -> dict:
    """Return the most recent polled sample as a flat key→value dict."""
    return {
        "points": request.app.state.latest_points,
        "ts": request.app.state.latest_ts,
        "quality": request.app.state.latest_quality,
        "connected": request.app.state.poller.connected if request.app.state.poller else False,
        "last_poll_ts": request.app.state.poller.last_poll_ts if request.app.state.poller else 0,
        "last_error": request.app.state.poller.last_error if request.app.state.poller else "",
    }
