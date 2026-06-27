"""
Sample dataclass + SampleBus pub/sub.

EnergipaysPoller emits Sample objects; subscribers (MetricsRecorder, MqttPublisher)
register callbacks and receive every sample as it arrives.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class Sample:
    device_id: str
    ts: float                        # unix timestamp
    points: dict[str, object]        # flat key→value dict of all telemetry
    quality: str = "ok"              # "ok" | "stale" | "error"


Subscriber = Callable[[Sample], Awaitable[None]]


class SampleBus:
    """Async pub/sub bus: publish a sample → all subscribers are awaited."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        self._subscribers.append(fn)

    def unsubscribe_type(self, cls: type) -> int:
        """Remove all subscribers whose owning object is an instance of cls. Returns count removed."""
        before = len(self._subscribers)
        self._subscribers = [
            fn for fn in self._subscribers
            if not isinstance(getattr(fn, "__self__", None), cls)
        ]
        return before - len(self._subscribers)

    async def publish(self, sample: Sample) -> None:
        tasks = [asyncio.create_task(fn(sample)) for fn in self._subscribers]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for exc in results:
                if isinstance(exc, Exception):
                    log.error("SampleBus subscriber error: %s", exc)
