"""MQTT integration subscriber — subscribes to configured topics, extracts JSON dot-path values."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..sample import Sample, SampleBus
from .base import EXT_DEVICE_ID
from .models import FieldMapping

log = logging.getLogger(__name__)


def _dotpath(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


class MqttSubscriber:
    """
    Subscribes to MQTT topics. Source format: "topic:json.path" or just "topic" for raw payload.
    Uses aiomqtt (already used by mqtt_publisher).
    """

    def __init__(
        self,
        integration_id: str,
        name: str,
        bus: SampleBus,
        host: str,
        port: int,
        username: str,
        password: str,
        mappings: list[FieldMapping],
    ) -> None:
        self.integration_id = integration_id
        self.name = name
        self.bus = bus
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.mappings = mappings
        self.status: str = "unknown"
        self.last_error: str = ""
        self._task = None
        self._stop_event = __import__("asyncio").Event()
        self._latest: dict[str, object] = {}

    async def start(self) -> None:
        import asyncio
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=f"mqtt-sub-{self.integration_id}")
        log.info("MQTT integration %r started", self.name)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except __import__("asyncio").CancelledError:
                pass

    async def _run(self) -> None:
        import asyncio
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
                log.warning("MQTT subscriber %r error: %s — retry in %.0fs", self.name, exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 120)

    async def _connect_and_subscribe(self) -> None:
        import asyncio
        import aiomqtt

        # Build topic → mapping list
        topic_map: dict[str, list[FieldMapping]] = {}
        for m in self.mappings:
            topic = m.source.split(":")[0]
            topic_map.setdefault(topic, []).append(m)

        kwargs: dict = {"hostname": self.host, "port": self.port}
        if self.username:
            kwargs["username"] = self.username
        if self.password:
            kwargs["password"] = self.password

        async with aiomqtt.Client(**kwargs) as client:
            for topic in topic_map:
                await client.subscribe(topic)
            self.status = "live"
            self.last_error = ""
            log.info("MQTT subscriber %r connected, subscribed to %d topics", self.name, len(topic_map))

            async for message in client.messages:
                if self._stop_event.is_set():
                    break
                topic_str = str(message.topic)
                if topic_str not in topic_map:
                    continue
                try:
                    payload_str = message.payload.decode()
                except Exception:
                    continue

                for m in topic_map[topic_str]:
                    parts = m.source.split(":", 1)
                    if len(parts) == 2 and parts[1]:
                        # JSON dot-path extraction
                        try:
                            obj = json.loads(payload_str)
                            raw = _dotpath(obj, parts[1])
                        except Exception:
                            raw = None
                    else:
                        raw = payload_str

                    if raw is not None:
                        try:
                            value = float(raw) * m.scale
                        except (TypeError, ValueError):
                            value = raw
                        self._latest[m.target_metric] = value

                pts = {f"ext.{k}": v for k, v in self._latest.items()}
                await self.bus.publish(Sample(device_id=EXT_DEVICE_ID, ts=time.time(), points=pts))
