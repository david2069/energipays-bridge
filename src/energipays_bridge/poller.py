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

#: Consecutive poll failures before raising a notification. 3 x 60s ~= 3 min,
#: long enough to ride out a transient Energipays blip without crying wolf.
_FAILURES_BEFORE_ALERT = 3


def _flatten(device_status: dict, statistics: dict, device_profile: dict | None = None) -> dict[str, object]:
    """Merge device_status and statistics into a flat points dict."""
    points: dict[str, object] = {}

    # --- device_status fields ---
    # The response may have a top-level list or a dict keyed by device_id.
    # device_status response: {"data": [{"id":..., "is_online":1, "status":{temps/power/...}, ...}]}
    status: dict = {}
    if isinstance(device_status, dict):
        data_list = device_status.get("data") or device_status.get("devices") or []
        if isinstance(data_list, list) and data_list:
            status = data_list[0] if isinstance(data_list[0], dict) else {}
        else:
            status = device_status
    elif isinstance(device_status, list) and device_status:
        status = device_status[0] if isinstance(device_status[0], dict) else {}

    # Live telemetry sits under status["status"] (a dict), NOT status itself
    # which may also have status=1 (int) for device on/off.
    _s = status.get("status")
    _td = status.get("telemetryData")
    td: dict = _s if isinstance(_s, dict) else (_td if isinstance(_td, dict) else status)
    if not isinstance(td, dict):
        log.warning("_flatten: could not locate telemetry dict in device_status — got %s", type(td).__name__)
        td = {}
    for key in (
        "waterTemperature1", "waterTemperature2", "waterTemperature3",
        "voltageA", "voltageB", "voltageC",
        "phasePowerA", "phasePowerB", "phasePowerC", "phasePower",
        "heaterStatus", "boostStatus", "stateOfCharge",
        "temperatureLimit", "currentDRM", "signalWiFi",
        "signalLora", "signalloRa", "signalLoRa",
        "offPeakStreamHeaterPower", "offPeakStreamExtraPower", "divertedPowerHeater", "divertedPowerExtra",
        "solarStreamA", "solarStreamB", "solarStreamC",
        "is_online", "boostAntibacterialTime", "boost_power", "errors",
    ):
        val = td.get(key)
        if val is not None:
            points[key] = val

    # Normalise LoRa signal — API uses signalLoRa, signalLora, or signalloRa across firmwares
    for alias in ("signalLoRa", "signalloRa", "signalLora"):
        if alias in points:
            points["signalLora"] = points.pop(alias)
            break
    # Remove any remaining variants that didn't win
    for alias in ("signalLoRa", "signalloRa"):
        points.pop(alias, None)

    # WCR weather scores and weather boost end time — top-level fields on the device status object
    for key in ("wcr_today_score", "wcr_tomorrow_score", "weather_boost_ends_at"):
        val = status.get(key)
        if val is not None:
            points[key] = val

    # Log errors if any non-zero value present
    errors = points.get("errors")
    if isinstance(errors, dict):
        # API returns string values like '0' — treat '0', 0, None, False as no error
        nonzero = {k: v for k, v in errors.items() if v and v != '0' and v != 0}
        if nonzero:
            log.warning("Device errors reported: %s", nonzero)
        points["errorCount"] = len(nonzero)
    else:
        points["errorCount"] = 0

    # computed solar power (sum of per-phase streams, in watts)
    streams = [points.get(k) for k in ("solarStreamA", "solarStreamB", "solarStreamC")]
    solar_vals = [v for v in streams if isinstance(v, (int, float))]
    if solar_vals:
        points["solarPower"] = round(sum(solar_vals), 1)

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

    # --- device profile fields (from GET /api/devices/{id}, full object) ---
    if isinstance(device_profile, dict):
        prof = device_profile
        # Unwrap {"data": {...}} or {"data": [{...}]} if needed
        raw_data = prof.get("data")
        if isinstance(raw_data, dict):
            prof = raw_data
        elif isinstance(raw_data, list) and raw_data:
            prof = raw_data[0]
        # Switcher states from status_data
        sd = prof.get("status_data") or {}
        if isinstance(sd, dict):
            for key in ("customer", "admin", "heaterStatus", "heaterPumpStatus",
                        "evStatus", "offpeakSwitcherStatus", "weatherSwitcherStatus",
                        "priorityModeSwitcherStatus", "smartWeatherSwitcherStatus",
                        "solarRestrictionSwitcherStatus", "offpeakTemperatureSwitcherStatus",
                        "heater2SwitcherStatus"):
                val = sd.get(key)
                if val is not None:
                    points[f"sd.{key}"] = val

        # Active rules per circuit — normalise "unknown" and "0" to ""
        def _norm_rule(val: object) -> str:
            s = str(val) if val is not None else ""
            return "" if s in ("unknown", "0", "null") else s

        points["active_rule_id"] = _norm_rule(
            sd.get("rule_customer")
            or (prof.get("device_statuses") or {}).get("customer_rule")
        )
        points["active_rule_offpeak_id"] = _norm_rule(sd.get("rule_offpeak"))
        points["active_rule_heater2_id"] = _norm_rule(sd.get("rule_heater2"))
        points["retailer_rule_id"] = _norm_rule(
            sd.get("rule_retailer")
            or (prof.get("device_statuses") or {}).get("retailer_rule")
        )

        # Solar restriction window times (top-level on profile)
        for key in ("solar_restriction_from", "solar_restriction_to"):
            val = prof.get(key)
            if val is not None:
                points[f"sd.{key}"] = val

        # Device-level fields
        for key in ("boost_power", "weather_boost_power", "weather_boost_time",
                    "volume", "name", "firmware_version", "is_online",
                    "phase_type", "phase_active", "off_grid", "timer_management",
                    "is_lora_enabled", "ws_serial_number", "pcb_version",
                    "ws_firmware_version", "ws_pcb_version",
                    "is_h2_load_active", "current_temperature",
                    "antibacterial_temperature", "offpeak_temperature",
                    "has_storage", "heater_separate_mode", "sum_power"):
            val = prof.get(key)
            if val is not None:
                points[f"dev.{key}"] = val

        # Storage info (nested dict)
        storage = prof.get("storage") or {}
        if isinstance(storage, dict):
            for key in ("name", "id"):
                val = storage.get(key)
                if val is not None:
                    points[f"dev.storage_{key}"] = val

        # LoRa status from status.statuses (LoRaSetting, LoraFreq, LoRaPower)
        lora_statuses = (prof.get("status") or {}).get("statuses") or {}
        if isinstance(lora_statuses, dict):
            for key in ("LoRaSetting", "LoraFreq", "LoRaPower"):
                val = lora_statuses.get(key)
                if val is not None:
                    points[f"lora.{key}"] = val

        # Device timezone + GPS
        tz = prof.get("tz")
        if tz:
            points["dev.timezone"] = tz
        loc = prof.get("location") or {}
        if isinstance(loc, dict):
            lat = loc.get("lat")
            lng = loc.get("lng") or loc.get("lon")
            if lat and lng and (lat != 0 or lng != 0):
                points["dev.latitude"] = lat
                points["dev.longitude"] = lng

        # User identity + location (stable, logged-in user)
        user = prof.get("user") or {}
        if isinstance(user, dict) and user.get("email"):
            points["user.email"] = user["email"]
            points["user.name"] = f"{user.get('name','')} {user.get('last_name','')}".strip()
            for field in ("address", "city", "state", "country", "zip", "phone_number"):
                val = user.get(field)
                if val is not None:
                    points[f"user.{field}"] = val

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
        self._watchdog: asyncio.Task | None = None
        self._stopping = False

        # Health-event callback: async fn(event_type: str, context: dict) -> None.
        # Set by main.py so the poller can raise HA notifications without
        # importing the DB/notification layer itself.
        self.on_health_event = None

        # A single poll must not be able to wedge the loop forever. The vendor
        # client runs on threads via asyncio.to_thread, and a thread blocked in
        # a socket read cannot be cancelled — wait_for at least lets the LOOP
        # move on. This is what silently stalled the HA add-on for 3.4 days in
        # Sep 2026: no exception, no log line, just a loop that never came back.
        self._poll_timeout = max(30, poll_interval * 2)
        # Declare a stall after this long with no SUCCESSFUL poll.
        self._stall_after = max(180, poll_interval * 5)

        self.connected: bool = False
        self.last_poll_ts: float = 0.0
        self.last_error: str = ""
        self.polls_total: int = 0
        self.consecutive_failures: int = 0
        self.restarts: int = 0
        self.stalled: bool = False

    async def _emit(self, event: str, context: dict) -> None:
        """Fire a health event, never letting a notification failure hurt polling."""
        cb = self.on_health_event
        if cb is None:
            return
        try:
            await cb(event, context)
        except Exception as exc:  # noqa: BLE001
            log.warning("health event %s not delivered: %s", event, exc)

    async def start(self) -> None:
        self._stopping = False
        self._spawn_loop()
        self._watchdog = asyncio.create_task(self._watch(), name="energipays-poller-watchdog")

    def _spawn_loop(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="energipays-poller")
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """A poll task must never die quietly. Before this existed, an exception
        escaping _loop() killed polling permanently with no log line at all."""
        if self._stopping or task.cancelled():
            return
        exc = task.exception()
        log.critical("Poller task exited unexpectedly (%s) — restarting", exc or "no exception")
        self.connected = False
        self.restarts += 1
        self._spawn_loop()

    async def stop(self) -> None:
        self._stopping = True
        for t in (self._watchdog, self._task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _watch(self) -> None:
        """Restart the poll loop if no successful poll lands within _stall_after.

        Covers the case the done-callback cannot: a loop that is alive but wedged
        inside a hung request.
        """
        while True:
            try:
                await asyncio.sleep(min(60, max(10, self._interval)))
                if self._stopping or self.last_poll_ts == 0.0:
                    continue
                stale = time.time() - self.last_poll_ts
                if stale > self._stall_after and not self.stalled:
                    self.stalled = True
                    log.critical(
                        "Poller STALLED — no successful poll for %.0fs (limit %ss); restarting",
                        stale, self._stall_after,
                    )
                    await self._emit("poller_stalled", {"stale_min": int(stale // 60)})
                    if self._task and not self._task.done():
                        self._task.cancel()
                        try:
                            await self._task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                    self.restarts += 1
                    self._spawn_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Poller watchdog error: %s", exc)

    async def _record_failure(self, error: str) -> None:
        """One place to count a failed poll, whatever path it failed on.

        A poll that times out or explodes past its own handler is just as
        invisible to the user as one that returns an error — so all three
        routes alert on the same threshold.
        """
        self.connected = False
        self.last_error = error
        self.consecutive_failures += 1
        if self.consecutive_failures == _FAILURES_BEFORE_ALERT:
            await self._emit("poll_failing",
                             {"count": self.consecutive_failures, "error": error[:200]})

    async def _loop(self) -> None:
        log.info("Poller starting (device=%s, interval=%ss)", self._device_id, self._interval)
        while True:
            try:
                await asyncio.wait_for(self._poll(), timeout=self._poll_timeout)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                log.warning("Poll timed out after %ss", self._poll_timeout)
                await self._record_failure(f"poll timed out after {self._poll_timeout}s")
            except Exception as exc:  # noqa: BLE001
                # _poll handles its own errors; this is the belt-and-braces layer
                # that keeps the loop alive no matter what escapes.
                log.warning("Poll raised past its own handler: %s", exc)
                await self._record_failure(str(exc))
            await asyncio.sleep(self._interval)

    async def _poll(self) -> None:
        try:
            device_status, statistics, device_profile = await asyncio.gather(
                asyncio.to_thread(self._client.device_status, [self._device_id]),
                asyncio.to_thread(self._client.statistics, self._device_id, self._data_server),
                asyncio.to_thread(self._client.device, self._device_id),
            )
            points = _flatten(device_status, statistics, device_profile)
            was_stalled, failures = self.stalled, self.consecutive_failures
            self.connected = True
            self.last_poll_ts = time.time()
            self.last_error = ""
            self.polls_total += 1
            self.consecutive_failures = 0
            self.stalled = False
            if was_stalled or failures >= _FAILURES_BEFORE_ALERT:
                log.info("Poller recovered after %d consecutive failures", failures)
                await self._emit("poller_recovered", {"failures": failures})
            sample = Sample(device_id=self._device_id, ts=self.last_poll_ts,
                            points=points, quality="ok")
            await self._bus.publish(sample)
            log.debug("Poll OK: %d points", len(points))
        except Exception as exc:
            # Alerts once on the way down, not every 60s forever.
            await self._record_failure(str(exc))
            log.warning("Poll failed (%d in a row): %s", self.consecutive_failures, exc)
            sample = Sample(device_id=self._device_id, ts=time.time(),
                            points={}, quality="error")
            await self._bus.publish(sample)
