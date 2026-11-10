"""Abstract base class for integration pollers."""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

from ..sample import Sample, SampleBus
from .normalise import derive

log = logging.getLogger(__name__)

EXT_DEVICE_ID = "ext"


async def publish_points(bus: SampleBus, points: dict[str, object]) -> None:
    """Fill derived values (#15), prefix with ``ext.`` and publish one sample.

    Every protocol publishes through here, so all get the same derivation.
    """
    derived = derive(dict(points))
    prefixed = {f"ext.{k}": v for k, v in derived.items()}
    await bus.publish(Sample(device_id=EXT_DEVICE_ID, ts=time.time(), points=prefixed))


class IntegrationPoller(ABC):
    """Base class for all integration pollers. Subclasses implement _poll()."""

    def __init__(self, integration_id: str, name: str, bus: SampleBus, poll_interval: int = 30) -> None:
        self.integration_id = integration_id
        self.name = name
        self.bus = bus
        self.poll_interval = poll_interval
        self.status: str = "unknown"
        self.last_error: str = ""
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name=f"integration-{self.integration_id}")
        log.info("Integration %r (%s) started", self.name, self.integration_id)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Integration %r stopped", self.name)

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                points = await self._poll()
                if points:
                    await publish_points(self.bus, points)
                    self.status = "live"
                    self.last_error = ""
            except Exception as exc:
                self.status = "offline"
                self.last_error = str(exc)
                log.warning("Integration %r poll error: %s", self.name, exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    @abstractmethod
    async def _poll(self) -> dict[str, object]:
        """Poll the integration source and return a flat dict of {metric_key: value}."""
        ...
