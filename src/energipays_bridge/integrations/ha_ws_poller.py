"""HA WebSocket poller — subscribes to state_changed events for mapped entities."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from ..sample import Sample, SampleBus
from .base import EXT_DEVICE_ID
from .models import FieldMapping

log = logging.getLogger(__name__)


class HaWsPoller:
    """
    Subscribes to HA WebSocket state_changed events.
    Unlike the base polling loop, this is event-driven rather than interval-based.
    Reconnects with exponential backoff on disconnect.
    """

    def __init__(
        self,
        integration_id: str,
        name: str,
        bus: SampleBus,
        url: str,
        token: str,
        mappings: list[FieldMapping],
    ) -> None:
        self.integration_id = integration_id
        self.name = name
        self.bus = bus
        # Convert http(s):// to ws(s):// if needed
        self.url = url.replace("http://", "ws://").replace("https://", "wss://")
        if not self.url.endswith("/api/websocket"):
            self.url = self.url.rstrip("/") + "/api/websocket"
        self.token = token
        self.mappings = {m.source: m for m in mappings}   # entity_id → mapping
        self.status: str = "unknown"
        self.last_error: str = ""
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._latest: dict[str, object] = {}

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=f"ha-ws-{self.integration_id}")
        log.info("HA WebSocket integration %r started", self.name)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        backoff = 5.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_subscribe()
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = "offline"
                self.last_error = str(exc)
                log.warning("HA WS %r disconnected: %s — retry in %.0fs", self.name, exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 120)

    async def _connect_and_subscribe(self) -> None:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                # Auth handshake
                msg = json.loads((await ws.receive()).data)
                if msg.get("type") != "auth_required":
                    raise ValueError(f"Expected auth_required, got {msg.get('type')}")

                await ws.send_json({"type": "auth", "access_token": self.token})
                msg = json.loads((await ws.receive()).data)
                if msg.get("type") != "auth_ok":
                    raise ValueError(f"HA auth failed: {msg.get('message', 'unknown')}")

                # Subscribe to state_changed
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                msg = json.loads((await ws.receive()).data)
                if not msg.get("success"):
                    raise ValueError(f"Subscribe failed: {msg}")

                self.status = "live"
                self.last_error = ""
                log.info("HA WS %r subscribed to state_changed", self.name)

                async for raw in ws:
                    if self._stop_event.is_set():
                        break
                    msg = json.loads(raw.data)
                    if msg.get("type") != "event":
                        continue
                    evt = msg.get("event", {})
                    if evt.get("event_type") != "state_changed":
                        continue
                    data = evt.get("data", {})
                    entity_id = data.get("entity_id", "")
                    if entity_id not in self.mappings:
                        continue
                    new_state = data.get("new_state") or {}
                    state_val = new_state.get("state")
                    m = self.mappings[entity_id]
                    try:
                        value = float(state_val) * m.scale
                    except (TypeError, ValueError):
                        value = state_val
                    self._latest[m.target_metric] = value

                    # Publish a sample with all currently known ext metrics
                    pts = {f"ext.{k}": v for k, v in self._latest.items()}
                    await self.bus.publish(Sample(device_id=EXT_DEVICE_ID, ts=time.time(), points=pts))
