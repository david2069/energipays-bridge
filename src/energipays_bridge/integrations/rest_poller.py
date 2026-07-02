"""REST integration poller — HTTP GET, JSON dot-path field extraction."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..sample import SampleBus
from .base import IntegrationPoller
from .models import FieldMapping

log = logging.getLogger(__name__)


def _dotpath(obj: Any, path: str) -> Any:
    """Traverse a nested dict using a dot-separated path. Returns None if missing."""
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            try:
                obj = obj[int(key)]
            except (ValueError, IndexError):
                return None
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
        raw = _dotpath(data, m.source)
        if raw is not None:
            try:
                result[m.target_metric] = float(raw) * m.scale
            except (TypeError, ValueError):
                result[m.target_metric] = raw
    return result
