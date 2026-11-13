"""
EnergipaysPoller — background asyncio task that polls the Energipays cloud API
and emits Sample objects to a SampleBus.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .sample import Sample, SampleBus

if TYPE_CHECKING:
    from energipays import EnergipaysClient

log = logging.getLogger(__name__)

#: Consecutive poll failures before raising a notification. 3 x 60s ~= 3 min,
#: long enough to ride out a transient Energipays blip without crying wolf.
_FAILURES_BEFORE_ALERT = 3


#: Telemetry that changes while the PowerDiverter is uploading: grid power and
#: the hot-water tank temperatures (#36).
_CONTACT_KEYS = ("phasePower", "phasePowerA", "phasePowerB", "phasePowerC",
                 "waterTemperature1", "waterTemperature2", "waterTemperature3")
#: Unchanged grid power and tank temperatures for this long = no contact.
DEVICE_STALE_S = 30 * 60
#: How far back the last cloud stats slot with data is looked for.
LAST_DATA_LOOKBACK_DAYS = 7


def last_data_slot(raw: object, tz: str) -> float | None:
    """Epoch of the latest stats slot holding any non-zero value, or None.

    The cloud has no last-seen field. A PowerDiverter keeps no data while
    offline, so its 15-minute stats slots stay all zero from the moment it
    stopped uploading (same rule as energipays-mobile #25). Slot keys are
    device-local wall time.
    """
    a = raw.get("analytics") if isinstance(raw, dict) else None
    if not isinstance(a, dict):
        return None
    best = None
    for key, vals in a.items():
        if not isinstance(vals, dict):
            continue
        if not any(isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0
                   for v in vals.values()):
            continue
        try:
            at = datetime.strptime(str(key), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(tz))
        except ValueError:
            continue
        ts = at.timestamp()
        if best is None or ts > best:
            best = ts
    return best


class DeviceContact:
    """Whether Energipays is still hearing from the PowerDiverter (#36).

    Lost contact when Energipays reports it offline (is_online 0), or when grid
    power and tank temperatures have not changed for DEVICE_STALE_S. A
    successful cloud poll says nothing about the device itself.
    """

    def __init__(self, stale_after_s: float = DEVICE_STALE_S) -> None:
        self.stale_after_s = stale_after_s
        self.connected: bool | None = None
        self.last_data_ts: float | None = None
        self._sig: tuple | None = None

    def update(self, points: dict, now: float) -> tuple[bool, bool]:
        """Return (connected, changed) after one poll."""
        sig = tuple(points.get(k) for k in _CONTACT_KEYS)
        if any(v is not None for v in sig) and sig != self._sig:
            if self._sig is not None or self.last_data_ts is None:
                self.last_data_ts = now
            self._sig = sig
        flag = points.get("is_online")
        reported_off = flag is not None and str(flag).strip().lower() in ("0", "false")
        stale = self.last_data_ts is None or now - self.last_data_ts > self.stale_after_s
        connected = not reported_off and not stale
        changed = connected != self.connected
        self.connected = connected
        return connected, changed


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

    # is_online is top-level on the device-status entry (per the response shape
    # above); fall back to the telemetry dict only if it is not there (#8).
    if status.get("is_online") is not None:
        points["is_online"] = status.get("is_online")

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
        if val is not None and key not in points:
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
        # energipays.com's "Last 14 days" is the ``fortnight`` bucket (#4); the
        # point prefix stays last14days so stored history and the UI keep working.
        for period_key, prefix in (("today", "today"), ("yesterday", "yesterday"),
                                   ("week", "week"), ("last7days", "last7days"),
                                   ("fortnight", "last14days")):
            period = statistics.get(period_key) or {}
            if not isinstance(period, dict):
                continue
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

        # Logged-in user: only the name and email the profile menu shows. The
        # address, city, state, country, postcode and phone are never stored:
        # points end up in logs, exports and bug reports (#7).
        user = prof.get("user") or {}
        if isinstance(user, dict) and user.get("email"):
            points["user.email"] = user["email"]
            points["user.name"] = f"{user.get('name','')} {user.get('last_name','')}".strip()

    return points


def _cloud_error(result: object) -> str:
    """The error text if an energipays-client call returned an error dict, else ''."""
    if isinstance(result, dict) and ("error" in result or "error_decrypted" in result):
        return str(result.get("error_decrypted") or result.get("body")
                   or result.get("error"))[:200]
    return ""


class EnergipaysPoller:
    def __init__(self, client: "EnergipaysClient", bus: SampleBus,
                 device_id: str, data_server: str, poll_interval: int = 60,
                 tz: str = "Australia/Sydney") -> None:
        self._client = client
        self._tz = tz
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
        self.contact = DeviceContact()

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

    async def _find_last_data(self) -> float | None:
        """Last cloud stats slot with data: today first, then the past week."""
        today = datetime.now(ZoneInfo(self._tz)).date()
        for start in (today, today - timedelta(days=LAST_DATA_LOOKBACK_DAYS)):
            try:
                raw = await asyncio.to_thread(self._client.stats, self._device_id,
                                              start.isoformat(), today.isoformat(),
                                              self._data_server, self._tz, "power", "sum")
            except Exception as exc:  # noqa: BLE001
                log.warning("Last-data lookup failed: %s", exc)
                return None
            ts = last_data_slot(raw, self._tz)
            if ts is not None:
                return ts
        return None

    async def _track_contact(self, points: dict) -> None:
        """Add device_connected / device_last_data_ts; log and notify on change."""
        c = self.contact
        connected, changed = c.update(points, self.last_poll_ts)
        if changed and not connected:
            found = await self._find_last_data()
            if found is not None:
                c.last_data_ts = found
            since = (datetime.fromtimestamp(c.last_data_ts, ZoneInfo(self._tz)).strftime("%H:%M %a %d %b")
                     if c.last_data_ts else f"more than {LAST_DATA_LOOKBACK_DAYS} days")
            log.warning("PowerDiverter not connected: Energipays has had no data from it since %s. "
                        "Check it is powered and connected to Wi-Fi.", since)
            await self._emit("device_offline", {"ts": since, "since": since})
        elif changed and connected and c.last_data_ts and self.polls_total > 1:
            log.info("PowerDiverter back online")
            await self._emit("device_online", {"ts": datetime.now(ZoneInfo(self._tz)).strftime("%H:%M")})
        points["device_connected"] = 1 if connected else 0
        if c.last_data_ts:
            points["device_last_data_ts"] = round(c.last_data_ts)
            points["device_last_data"] = datetime.fromtimestamp(
                round(c.last_data_ts), ZoneInfo(self._tz)).isoformat()

    async def _poll(self) -> None:
        try:
            device_status, statistics, device_profile = await asyncio.gather(
                asyncio.to_thread(self._client.device_status, [self._device_id]),
                asyncio.to_thread(self._client.statistics, self._device_id,
                                  self._data_server, self._tz),
                asyncio.to_thread(self._client.device, self._device_id),
            )
            # The client returns an error dict rather than raising. A failed status
            # read is a failed poll; a failed statistics or profile read is logged
            # and left out, so it can't be counted as fresh data (#8).
            err = _cloud_error(device_status)
            if err:
                raise RuntimeError(f"device status: {err}")
            for name, part in (("statistics", statistics), ("profile", device_profile)):
                perr = _cloud_error(part)
                if perr:
                    log.warning("Poll: %s read failed (%s); left out of this sample", name, perr)
            statistics = None if _cloud_error(statistics) else statistics
            device_profile = None if _cloud_error(device_profile) else device_profile
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
            await self._track_contact(points)
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
