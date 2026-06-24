"""
EnergipaysPoller — background asyncio task that polls the Energipays cloud API
and emits Sample objects to a SampleBus.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .sample import Sample, SampleBus

if TYPE_CHECKING:
    from energipays import EnergipaysClient

log = logging.getLogger(__name__)


def _flatten(device_status: dict, statistics: dict) -> dict[str, object]:
    """Merge device_status and statistics into a flat points dict."""
    points: dict[str, object] = {}

    # --- device_status fields ---
    # The response may have a top-level list or a dict keyed by device_id.
    status: dict = {}
    if isinstance(device_status, list) and device_status:
        status = device_status[0] if isinstance(device_status[0], dict) else {}
    elif isinstance(device_status, dict):
        # May be wrapped: {data: [{...}]} or the device dict directly
        data = device_status.get("data") or device_status.get("devices")
        if isinstance(data, list) and data:
            status = data[0]
        else:
            status = device_status

    td = status.get("telemetryData") or status.get("status_data") or status
    for key in (
        "waterTemperature1", "waterTemperature2", "waterTemperature3",
        "voltageA", "voltageB", "voltageC",
        "phasePowerA", "phasePowerB", "phasePowerC", "phasePower",
        "heaterStatus", "boostStatus", "stateOfCharge",
        "temperatureLimit", "currentDRM", "signalWiFi",
        "offPeakStreamHeaterPower", "solarStreamA", "solarStreamB", "solarStreamC",
        "is_online",
    ):
        val = td.get(key)
        if val is not None:
            points[key] = val

    # computed average temperature
    t1 = points.get("waterTemperature1")
    t2 = points.get("waterTemperature2")
    t3 = points.get("waterTemperature3")
    temps = [v for v in (t1, t2, t3) if isinstance(v, (int, float))]
    if temps:
        points["waterTemperatureAvg"] = round(sum(temps) / len(temps), 1)

    # --- statistics fields ---
    if isinstance(statistics, dict):
        for period_key in ("today", "yesterday", "week", "last7days", "last14days"):
            period = statistics.get(period_key) or {}
            if not isinstance(period, dict):
                continue
            prefix = period_key
            for stat_key in ("EEct", "IEct", "DE_h", "DE_e", "OIE_h",
                              "IEct_max", "IEct_min", "IEct_avg"):
                val = period.get(stat_key)
                if val is not None:
                    points[f"{prefix}.{stat_key}"] = val

    return points


class EnergipaysPoller:
    def __init__(self, client: "EnergipaysClient", bus: SampleBus,
                 device_id: str, data_server: str, poll_interval: int = 60) -> None:
        self._client = client
        self._bus = bus
        self._device_id = device_id
        self._data_server = data_server
        self._interval = poll_interval
        self._task: asyncio.Task | None = None

        self.connected: bool = False
        self.last_poll_ts: float = 0.0
        self.last_error: str = ""
        self.polls_total: int = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="energipays-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        log.info("Poller starting (device=%s, interval=%ss)", self._device_id, self._interval)
        while True:
            await self._poll()
            await asyncio.sleep(self._interval)

    async def _poll(self) -> None:
        try:
            device_status, statistics = await asyncio.gather(
                asyncio.to_thread(self._client.device_status, [self._device_id]),
                asyncio.to_thread(self._client.statistics, self._device_id, self._data_server),
            )
            points = _flatten(device_status, statistics)
            self.connected = True
            self.last_poll_ts = time.time()
            self.last_error = ""
            self.polls_total += 1
            sample = Sample(device_id=self._device_id, ts=self.last_poll_ts,
                            points=points, quality="ok")
            await self._bus.publish(sample)
            log.debug("Poll OK: %d points", len(points))
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            log.warning("Poll failed: %s", exc)
            sample = Sample(device_id=self._device_id, ts=time.time(),
                            points={}, quality="error")
            await self._bus.publish(sample)
