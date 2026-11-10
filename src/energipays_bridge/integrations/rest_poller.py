"""REST integration poller — HTTP GET, JSON dot-path field extraction."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..sample import SampleBus
from .base import IntegrationPoller
from .models import FieldMapping
from .normalise import convert

log = logging.getLogger(__name__)


def _dotpath(obj: Any, path: str) -> Any:
    """Traverse nested dicts/lists by a dot-separated path. Returns None if missing.

    Keys may themselves contain dots (modbus-bridge points are keyed "714.DCW",
    "label.home_load_power"): at each level the longest run of segments that is a key
    wins, so "points.714.DCW.value" finds points["714.DCW"]["value"] (#15).
    """
    parts = path.split(".")
    i = 0
    while i < len(parts):
        if isinstance(obj, dict):
            for j in range(len(parts), i, -1):
                key = ".".join(parts[i:j])
                if key in obj:
                    obj, i = obj[key], j
                    break
            else:
                return None
        elif isinstance(obj, list):
            try:
                obj = obj[int(parts[i])]
            except (ValueError, IndexError):
                return None
            i += 1
        else:
            return None
    return obj


class RestPoller(IntegrationPoller):
    def __init__(
        self,
        integration_id: str,
        name: str,
        bus: SampleBus,
        base_url: str,
        endpoint: str,
        mappings: list[FieldMapping],
        auth_type: str = "none",
        auth_token: str = "",
        poll_interval: int = 30,
    ) -> None:
        super().__init__(integration_id, name, bus, poll_interval)
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.mappings = mappings
        self.auth_type = auth_type
        self.auth_token = auth_token

    def _build_headers(self) -> dict:
        if self.auth_type == "bearer" and self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        if self.auth_type == "basic" and self.auth_token:
            return {"Authorization": f"Basic {self.auth_token}"}
        return {}

    async def _poll(self) -> dict[str, object]:
        url = self.base_url + self.endpoint
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=self._build_headers())
            r.raise_for_status()
            data = r.json()

        return _extract_mappings(data, self.mappings)


def _extract_mappings(data: Any, mappings: list[FieldMapping]) -> dict[str, object]:
    result: dict[str, object] = {}
    for m in mappings:
        if not m.enabled:
            continue
        raw = _dotpath(data, m.source)
        if raw is not None:
            result[m.target_metric] = convert(m, raw)
    return result
