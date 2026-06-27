from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Request

log = logging.getLogger("energipays_bridge.http")

router = APIRouter(prefix="/api/http-stats", tags=["http-stats"])


@dataclass
class HttpMetrics:
    started_ts: float = field(default_factory=time.time)
    total_requests: int = 0
    last_request_ts: float | None = None
    error_count: int = 0
    last_error_ts: float | None = None
    last_error: str = ""


def attach_metrics_hook(client, metrics: HttpMetrics) -> None:
    """Attach a response hook to the EnergipaysClient session."""

    def _hook(response, *args, **kwargs):
        metrics.total_requests += 1
        metrics.last_request_ts = time.time()
        if not response.ok:
            metrics.error_count += 1
            metrics.last_error_ts = time.time()
            method = getattr(response.request, "method", "?")
            url = getattr(response, "url", "?")
            # Keep URL readable but not too long
            url_short = str(url)
            if len(url_short) > 100:
                url_short = url_short[:100] + "…"
            desc = f"HTTP {response.status_code} {response.reason} — {method} {url_short}"
            metrics.last_error = desc
            log.warning("API error: %s", desc)

    client.session.hooks["response"].append(_hook)


@router.get("")
async def get_http_stats(request: Request) -> dict:
    m: HttpMetrics | None = getattr(request.app.state, "http_metrics", None)
    if m is None:
        return {"available": False}
    return {
        "available": True,
        "started_ts": m.started_ts,
        "total_requests": m.total_requests,
        "last_request_ts": m.last_request_ts,
        "error_count": m.error_count,
        "last_error_ts": m.last_error_ts,
        "last_error": m.last_error,
    }
