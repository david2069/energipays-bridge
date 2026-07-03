"""NotificationTrigger — SampleBus subscriber that detects state transitions."""
from __future__ import annotations

import logging
from datetime import datetime

from ..sample import Sample
from .dispatcher import send_notification

log = logging.getLogger(__name__)

_HYSTERESIS_C = 2.0   # temp must move this far from threshold before re-triggering


class NotificationTrigger:
    """Detects telemetry transitions and dispatches push notifications."""

    def __init__(self, db, device_id: str = "") -> None:
        self._db = db
        self._device_id = device_id
        self._prev: dict = {}
        self._temp_triggered = False   # True after threshold crossed; reset by hysteresis

    async def __call__(self, sample: Sample) -> None:
        # Skip integration ext.* samples — only react to main device telemetry
        if self._device_id and sample.device_id != self._device_id:
            return
        p = sample.points
        prev = self._prev

        db = self._db

        if not prev:
            # First sample — record state but don't fire.
            # Pre-initialize _temp_triggered so a temp already above threshold
            # on boot doesn't fire on the very next poll.
            temp0 = p.get("waterTemperatureAvg")
            if temp0 is not None:
                from ..store.db import get_notification_settings
                ns = await get_notification_settings(db)
                threshold0 = float(ns.get("temp_threshold", 40.0))
                self._temp_triggered = temp0 >= threshold0
            self._prev = dict(p)
            return

        now_str = datetime.now().strftime("%H:%M")

        # ── Device online / offline ───────────────────────────────────────────
        if p.get("is_online") != prev.get("is_online") and p.get("is_online") is not None:
            ev = "device_online" if p["is_online"] else "device_offline"
            await send_notification(db, ev, {"ts": now_str})

        # ── Boost started / ended ─────────────────────────────────────────────
        prev_boost = bool(prev.get("boostStatus", 0))
        curr_boost = bool(p.get("boostStatus", 0))
        if curr_boost != prev_boost:
            ev = "boost_started" if curr_boost else "boost_ended"
            await send_notification(db, ev, {})

        # ── Off-peak rule started / ended ─────────────────────────────────────
        prev_op = prev.get("active_rule_offpeak_id")
        curr_op = p.get("active_rule_offpeak_id")
        if curr_op != prev_op:
            if curr_op and not prev_op:
                await send_notification(db, "offpeak_started", {})
            elif not curr_op and prev_op:
                await send_notification(db, "offpeak_ended", {})

        # ── Temperature threshold ─────────────────────────────────────────────
        temp = p.get("waterTemperatureAvg")
        if temp is not None:
            from ..store.db import get_notification_settings
            ns = await get_notification_settings(db)
            threshold = float(ns.get("temp_threshold", 40.0))
            if not self._temp_triggered and temp >= threshold:
                self._temp_triggered = True
                t1 = p.get("waterTemperature1")
                t2 = p.get("waterTemperature2")
                t3 = p.get("waterTemperature3")
                await send_notification(db, "temp_threshold", {
                    "temp": f"{temp:.1f}",
                    "threshold": f"{threshold:.0f}",
                    "t1": f"{t1:.1f}" if t1 is not None else "—",
                    "t2": f"{t2:.1f}" if t2 is not None else "—",
                    "t3": f"{t3:.1f}" if t3 is not None else "—",
                })
            elif self._temp_triggered and temp < threshold - _HYSTERESIS_C:
                self._temp_triggered = False   # reset; will re-fire next crossing

        # ── Done heating (heaterStatus goes off after boost completed) ────────
        prev_heater = bool(prev.get("heaterStatus", 0))
        curr_heater = bool(p.get("heaterStatus", 0))
        if prev_heater and not curr_heater and prev_boost:
            temp_val = p.get("waterTemperatureAvg") or p.get("waterTemperature3")
            await send_notification(db, "done_heating", {
                "temp": f"{temp_val:.1f}" if temp_val else "—"
            })

        self._prev = dict(p)
