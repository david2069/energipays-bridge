from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from energipays_bridge.publish.entities import (
    BOOST_DURATION_OPTIONS,
    BOOST_POWER_MAP,
    BOOST_POWER_OPTIONS,
    BOOST_POWER_REVERSE,
    ENERGIPAYS_ENTITIES,
    ENTITY_BY_SLUG,
    EntityDef,
)
from energipays_bridge.sample import Sample

log = logging.getLogger(__name__)

_QUEUE_MAX = 500
_RECONNECT_MAX = 60

# Entities only meaningful for multi-phase devices — suppressed when phase_type == 1
_MULTIPHASE_SLUGS = {"voltage_a", "voltage_b", "voltage_c", "power_a", "power_b", "power_c"}


class MqttPublisher:
    """Queue-based async MQTT publisher with HA Discovery and command handling."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        discovery_prefix: str = "homeassistant",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls
        self._discovery_prefix = discovery_prefix

        self._queue: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._client: mqtt.Client | None = None
        self._connected = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # device context (set on first sample)
        self._device_id: str | None = None
        self._device_info: dict | None = None
        self._discovery_sent = False
        self._latest_points: dict = {}

        # command dispatch (set from main after login)
        self._ep_client: Any = None    # EnergipaysClient
        self._data_server: str = ""

        # rule name ↔ id mapping
        self._rules: list[dict] = []   # [{id, name}, ...]
        self._rule_name_to_id: dict[str, str] = {}

        # local state for entities with no API-readable state
        self._boost_duration_sel: str = BOOST_DURATION_OPTIONS[1]  # "1 hour" (period=2)

        # last command result feedback
        self._last_cmd_result: str = ""
        self._last_cmd_ts: str = ""

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="mqtt_publisher")
        log.info("MqttPublisher started → %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await asyncio.to_thread(self._client.disconnect)
        log.info("MqttPublisher stopped")

    def set_client(self, client, device_id: str, data_server: str) -> None:
        """Wire in the EnergipaysClient for command dispatch."""
        self._ep_client = client
        self._data_server = data_server
        if self._device_id is None:
            self._device_id = device_id
        # Subscribe to command topics now if already connected
        if self._connected and self._client:
            self._client.subscribe(f"energipays/{device_id}/cmd/#")
            log.info("MQTT: subscribed to cmd topics for device %s", device_id)

    async def set_rules(self, rules: list[dict]) -> None:
        """Update rule options and re-send discovery for active_rule select."""
        self._rules = rules
        self._rule_name_to_id = {r["name"]: r["id"] for r in rules if r.get("name")}
        if self._connected and self._device_id and self._device_info:
            await self._publish_rule_discovery()

    # ── SampleBus subscriber ───────────────────────────────────────────────

    async def queue_sample(self, sample: Sample) -> None:
        pts = sample.points
        self._latest_points = pts

        if self._device_id is None:
            self._device_id = sample.device_id
        if self._device_info is None:
            self._device_info = {
                "identifiers": [f"energipays_{sample.device_id}"],
                "name": pts.get("dev.name", "Energipays Device"),
                "manufacturer": "Energipays",
                "model": "Power Diverter",
                "sw_version": pts.get("dev.firmware_version", ""),
                "serial_number": pts.get("dev.ws_serial_number", ""),
            }

        if not self._discovery_sent and self._connected:
            await self._enqueue_discovery()

        # State messages for all read-capable entities
        is_single_phase = int(pts.get("dev.phase_type", 0)) == 1
        for entity in ENERGIPAYS_ENTITIES:
            if not entity.stat_key:
                continue
            if is_single_phase and entity.slug in _MULTIPHASE_SLUGS:
                continue
            raw = pts.get(entity.stat_key)

            # Special formatting for selects
            if entity.slug == "boost_power_sel":
                idx = pts.get("dev.boost_power")
                value = BOOST_POWER_MAP.get(int(idx)) if idx else None
            elif entity.slug == "active_rule":
                rid = pts.get("active_rule_id")
                rule = next((r for r in self._rules if r["id"] == rid), None)
                value = rule["name"] if rule else "No Rule Set"
            else:
                value = entity.format_value(raw)

            if value is None:
                continue
            await self._enqueue(entity.state_topic(sample.device_id), value, retain=True)

        # Local-state entities (no API read)
        if self._device_id:
            await self._enqueue(
                ENTITY_BY_SLUG["boost_duration"].state_topic(self._device_id),
                self._boost_duration_sel, retain=True
            )

    # ── public API ─────────────────────────────────────────────────────────

    async def republish(self) -> None:
        self._discovery_sent = False
        if self._connected and self._device_id:
            await self._enqueue_discovery()
            # Re-publish current states immediately so HA doesn't show "unknown"
            if self._latest_points:
                dummy = Sample(
                    device_id=self._device_id,
                    ts=time.time(),
                    points=self._latest_points,
                    quality="ok",
                )
                await self.queue_sample(dummy)

    async def unpublish(self) -> None:
        if not self._device_id:
            return
        for entity in ENERGIPAYS_ENTITIES:
            topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                     f"/energipays_{self._device_id}_{entity.slug}/config")
            await self._enqueue(topic, "", retain=True)
        log.info("Unpublish: cleared %d discovery topics", len(ENERGIPAYS_ENTITIES))

    @property
    def connected(self) -> bool:
        return self._connected

    # ── discovery ─────────────────────────────────────────────────────────

    async def _enqueue_discovery(self) -> None:
        if not self._device_id or not self._device_info:
            return
        rule_options = (["No Rule Set"] + [r["name"] for r in self._rules if r.get("name")]
                        if self._rules else ["No Rule Set"])
        is_single_phase = int(self._latest_points.get("dev.phase_type", 0)) == 1
        count = 0
        for entity in ENERGIPAYS_ENTITIES:
            if is_single_phase and entity.slug in _MULTIPHASE_SLUGS:
                # Clear any previously registered discovery for this entity
                topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                         f"/energipays_{self._device_id}_{entity.slug}/config")
                await self._enqueue(topic, "", retain=True)
                continue
            opts = rule_options if entity.slug == "active_rule" else None
            payload = entity.discovery_payload(self._device_id, self._device_info,
                                               options_override=opts)
            topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                     f"/energipays_{self._device_id}_{entity.slug}/config")
            await self._enqueue(topic, json.dumps(payload), retain=True)
            count += 1
        self._discovery_sent = True
        log.info("Discovery queued for %s (%d entities)", self._device_id, count)

    async def _publish_rule_discovery(self) -> None:
        """Re-send only the active_rule select discovery (when rules list changes)."""
        entity = ENTITY_BY_SLUG["active_rule"]
        rule_options = ["No Rule Set"] + [r["name"] for r in self._rules if r.get("name")]
        payload = entity.discovery_payload(self._device_id, self._device_info,
                                           options_override=rule_options)
        topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                 f"/energipays_{self._device_id}_{entity.slug}/config")
        await self._enqueue(topic, json.dumps(payload), retain=True)

    # ── command handling ───────────────────────────────────────────────────

    async def _publish_cmd_result(self, message: str) -> None:
        """Publish last command result + ISO timestamp to HA feedback sensors and app log."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._last_cmd_result = message
        self._last_cmd_ts = ts
        if "FAILED" in message:
            log.warning("cmd: %s", message)
        else:
            log.info("cmd: %s", message)
        if not self._device_id:
            return
        await self._enqueue(
            ENTITY_BY_SLUG["last_cmd_result"].state_topic(self._device_id),
            message, retain=True
        )
        await self._enqueue(
            ENTITY_BY_SLUG["last_cmd_ts"].state_topic(self._device_id),
            ts, retain=True
        )

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        """Called from paho thread — dispatch to asyncio event loop."""
        if self._loop is None:
            return
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        asyncio.run_coroutine_threadsafe(
            self._handle_command(msg.topic, payload), self._loop
        )

    async def _handle_command(self, topic: str, payload: str) -> None:
        if not self._device_id:
            return
        # topic: energipays/{device_id}/cmd/{slug}
        prefix = f"energipays/{self._device_id}/cmd/"
        if not topic.startswith(prefix):
            return
        slug = topic[len(prefix):]
        log.info("cmd (MQTT): %s = %r", slug, payload)

        try:
            if slug == "power_diverter_sw":
                val = 1 if payload == "ON" else 0
                await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, status=val
                )
                await self._enqueue(
                    ENTITY_BY_SLUG["power_diverter_sw"].state_topic(self._device_id),
                    payload, retain=True
                )
                await self._publish_cmd_result(
                    f"Power diverter → {'ON' if val else 'OFF'}: OK"
                )

            elif slug == "heater_sw":
                val = 1 if payload == "ON" else 0
                await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, heaterStatus=val
                )
                await self._enqueue(
                    ENTITY_BY_SLUG["heater_sw"].state_topic(self._device_id),
                    payload, retain=True
                )
                await self._publish_cmd_result(
                    f"Immersion heater → {'ON' if val else 'OFF'}: OK"
                )

            elif slug in ("offpeak_switcher", "weather_switcher",
                          "solar_restriction", "priority_mode"):
                _field_map = {
                    "offpeak_switcher":  "offpeakSwitcherStatus",
                    "weather_switcher":  "weatherSwitcherStatus",
                    "solar_restriction": "solarRestrictionSwitcherStatus",
                    "priority_mode":     "priorityModeSwitcherStatus",
                }
                api_field = _field_map[slug]
                val = 1 if payload == "ON" else 0
                await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, **{api_field: val}
                )
                await self._enqueue(
                    ENTITY_BY_SLUG[slug].state_topic(self._device_id),
                    payload, retain=True
                )
                await self._publish_cmd_result(
                    f"{ENTITY_BY_SLUG[slug].name} → {'ON' if val else 'OFF'}: OK"
                )

            elif slug == "boost_active":
                if payload == "On":
                    period_map = {"30 min": 1, "1 hour": 2, "2 hours": 3}
                    period = period_map.get(self._boost_duration_sel, 2)
                    await asyncio.to_thread(
                        self._ep_client.boost_device,
                        self._device_id, self._data_server, period
                    )
                    await self._enqueue(
                        ENTITY_BY_SLUG["boost_active"].state_topic(self._device_id),
                        "On", retain=True
                    )
                    await self._publish_cmd_result(
                        f"Boost started: {self._boost_duration_sel}: OK"
                    )
                elif payload == "Off":
                    await asyncio.to_thread(
                        self._ep_client.cancel_boost,
                        self._device_id, self._data_server
                    )
                    await self._enqueue(
                        ENTITY_BY_SLUG["boost_active"].state_topic(self._device_id),
                        "Off", retain=True
                    )
                    await self._publish_cmd_result("Boost cancelled: OK")

            elif slug == "boost_duration":
                if payload in BOOST_DURATION_OPTIONS:
                    self._boost_duration_sel = payload
                    await self._enqueue(
                        ENTITY_BY_SLUG["boost_duration"].state_topic(self._device_id),
                        payload, retain=True
                    )
                    # Local-only: no API call needed, no result feedback

            elif slug == "boost_power_sel":
                if payload in BOOST_POWER_OPTIONS:
                    idx = BOOST_POWER_REVERSE[payload]
                    await asyncio.to_thread(
                        self._ep_client.set_boost_power,
                        self._device_id, idx
                    )
                    await self._enqueue(
                        ENTITY_BY_SLUG["boost_power_sel"].state_topic(self._device_id),
                        payload, retain=True
                    )
                    await self._publish_cmd_result(
                        f"Boost Power → {payload} (idx={idx}): OK"
                    )

            elif slug == "boost_start":
                period_map = {"30 min": 1, "1 hour": 2, "2 hours": 3}
                period = period_map.get(self._boost_duration_sel, 1)
                await asyncio.to_thread(
                    self._ep_client.boost_device,
                    self._device_id, self._data_server, period
                )
                log.info("cmd (MQTT): Boost → %s (period=%d): OK", self._boost_duration_sel, period)
                await self._publish_cmd_result(
                    f"Boost started: {self._boost_duration_sel} (period={period}): OK"
                )

            elif slug == "active_rule":
                if payload in ("No Rule Set", "None"):
                    rule_id = "0"
                else:
                    rule_id = self._rule_name_to_id.get(payload)
                    if not rule_id:
                        log.warning("active_rule: unknown rule name %r", payload)
                        await self._publish_cmd_result(
                            f"Active rule → {payload!r}: FAILED (unknown rule)"
                        )
                        return
                # Build status_data from latest points
                sd_fields = {
                    "admin": 1, "customer": 1, "retailer": 1,
                    "heaterStatus":              int(self._latest_points.get("sd.heaterStatus", 0)),
                    "offpeakSwitcherStatus":      int(self._latest_points.get("sd.offpeakSwitcherStatus", 0)),
                    "weatherSwitcherStatus":      int(self._latest_points.get("sd.weatherSwitcherStatus", 0)),
                    "solarRestrictionSwitcherStatus": int(self._latest_points.get("sd.solarRestrictionSwitcherStatus", 0)),
                    "priorityModeSwitcherStatus": int(self._latest_points.get("sd.priorityModeSwitcherStatus", 0)),
                    "smartWeatherSwitcherStatus": int(self._latest_points.get("sd.smartWeatherSwitcherStatus", 0)),
                    "offpeakTemperatureSwitcherStatus": int(self._latest_points.get("sd.offpeakTemperatureSwitcherStatus", 0)),
                    "heater2SwitcherStatus":      int(self._latest_points.get("sd.heater2SwitcherStatus", 0)),
                    "heaterPumpStatus":           int(self._latest_points.get("sd.heaterPumpStatus", 0)),
                    "evStatus": 0,
                }
                await asyncio.to_thread(
                    self._ep_client.update_device,
                    self._device_id, sd_fields, rule_id
                )
                log.info("cmd (MQTT): Active rule → %r (%s): OK", payload, rule_id)
                # Reflect immediately; confirmed state will arrive in next sample
                await self._enqueue(
                    ENTITY_BY_SLUG["active_rule"].state_topic(self._device_id),
                    payload, retain=True
                )
                await self._publish_cmd_result(
                    f"Active rule → {payload!r}: OK"
                )

        except Exception as exc:
            log.error("Command handler error [%s]: %s", slug, exc)
            await self._publish_cmd_result(
                f"{slug} → {payload!r}: FAILED ({type(exc).__name__}: {exc})"
            )

    # ── internals ─────────────────────────────────────────────────────────

    async def _enqueue(self, topic: str, payload: str, retain: bool = False) -> None:
        try:
            self._queue.put_nowait((topic, payload, retain))
        except asyncio.QueueFull:
            log.warning("MQTT queue full — dropping %s", topic)

    async def _run(self) -> None:
        backoff = 2.0
        while True:
            try:
                await asyncio.to_thread(self._connect)
                backoff = 2.0
                await self._drain_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._discovery_sent = False
                log.error("MQTT error: %s — reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

    def _connect(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id="energipays-bridge")
        if self._username:
            client.username_pw_set(self._username, self._password)
        if self._tls:
            client.tls_set()

        def on_connect(c, userdata, flags, rc, props=None):
            if rc == 0:
                self._connected = True
                self._discovery_sent = False
                # Subscribe to all command topics
                if self._device_id:
                    c.subscribe(f"energipays/{self._device_id}/cmd/#")
                log.info("MQTT connected to %s:%s", self._host, self._port)
            else:
                log.error("MQTT connect failed rc=%s", rc)

        def on_disconnect(c, userdata, rc, props=None, reason=None):
            self._connected = False
            log.warning("MQTT disconnected rc=%s", rc)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = self._on_message
        client.connect(self._host, self._port, keepalive=60)
        client.loop_start()
        self._client = client

        deadline = time.monotonic() + 10
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError(f"Cannot connect to MQTT broker at {self._host}:{self._port}")

        # Subscribe if device_id already known (reconnect case)
        if self._device_id:
            self._client.subscribe(f"energipays/{self._device_id}/cmd/#")

    async def _drain_loop(self) -> None:
        while True:
            topic, payload, retain = await self._queue.get()
            if self._client and self._connected:
                await asyncio.to_thread(
                    self._client.publish, topic, payload, qos=1, retain=retain
                )
            self._queue.task_done()
            await asyncio.sleep(0)

