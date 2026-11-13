from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from energipays_bridge.boost import DEFAULT_DURATION, DURATIONS
from energipays_bridge.cloud_result import label, outcome
from energipays_bridge.integrations.base import EXT_DEVICE_ID
from energipays_bridge.publish.entities import (
    BATTERY_ENTITIES,
    BOOST_POWER_MAP,
    BOOST_POWER_OPTIONS,
    BOOST_POWER_REVERSE,
    ENERGIPAYS_ENTITIES,
    ENTITY_BY_SLUG,
    EntityDef,
    device_availability_topic,
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
        read_only: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls
        self._discovery_prefix = discovery_prefix
        self._read_only = read_only

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
        self._latest_ext: dict = {}          # battery integration (ext.*) points (#20)
        self._ext_discovered: set[str] = set()

        # command dispatch (set from main after login)
        self._ep_client: Any = None    # EnergipaysClient
        self._data_server: str = ""

        # runtime pause flag (toggled via API without restart)
        self.paused: bool = False

        # rule name ↔ id mapping
        self._rules: list[dict] = []   # [{id, name}, ...]
        self._rule_name_to_id: dict[str, str] = {}

        # local state for entities with no API-readable state
        self._boost_duration_sel: str = DEFAULT_DURATION  # "1 hour" (period=2)

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

    def set_device(self, device_id: str, data_server: str) -> None:
        """Point commands at another device after a device switch (#8)."""
        old = self._device_id
        self._data_server = data_server
        if old == device_id:
            return
        self._device_id = device_id
        self._device_info = None        # rebuilt from the new device's next sample
        self._discovery_sent = False
        if self._client and self._connected:
            if old:
                self._client.unsubscribe(f"energipays/{old}/cmd/#")
            self._client.subscribe(f"energipays/{device_id}/cmd/#")
        log.info("MQTT: command target switched %s -> %s", old, device_id)

    async def _report(self, description: str, result: object) -> bool:
        """Publish the real outcome of a cloud call; True only when it was accepted."""
        kind, detail = outcome(result)
        msg = f"{description}: {label(kind, detail)}"
        (log.warning if kind in ("error", "skipped") else log.info)("cmd (MQTT): %s", msg)
        await self._publish_cmd_result(msg)
        return kind in ("ok", "queued")

    async def set_rules(self, rules: list[dict]) -> None:
        """Update rule options and re-send discovery for active_rule select."""
        self._rules = rules
        self._rule_name_to_id = {r["name"]: r["id"] for r in rules if r.get("name")}
        if self._connected and self._device_id and self._device_info:
            await self._publish_rule_discovery()

    # ── SampleBus subscriber ───────────────────────────────────────────────

    async def queue_sample(self, sample: Sample) -> None:
        if self.paused:
            return
        if sample.device_id == EXT_DEVICE_ID:
            # Battery integration samples feed the battery entities only; they must
            # not become the HA device or replace the PowerDiverter's points.
            self._latest_ext = dict(sample.points)
            await self._publish_ext()
            return
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

        # PowerDiverter contact (#36): readings entities go unavailable while offline
        if "device_connected" in pts:
            await self._enqueue(device_availability_topic(sample.device_id),
                                "online" if pts["device_connected"] else "offline", retain=True)

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
        for entity in ENERGIPAYS_ENTITIES + BATTERY_ENTITIES:
            topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                     f"/energipays_{self._device_id}_{entity.slug}/config")
            await self._enqueue(topic, "", retain=True)
        self._ext_discovered.clear()
        log.info("Unpublish: cleared %d discovery topics", len(ENERGIPAYS_ENTITIES) + len(BATTERY_ENTITIES))

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
        self._ext_discovered.clear()
        await self._publish_ext()

    async def _publish_ext(self) -> None:
        """Battery entities: discovery the first time a metric arrives, then state."""
        if not (self._connected and self._device_id and self._device_info and self._discovery_sent):
            return
        for entity in BATTERY_ENTITIES:
            if entity.stat_key not in self._latest_ext:
                continue
            if entity.slug not in self._ext_discovered:
                topic = (f"{self._discovery_prefix}/{entity.ha_type}"
                         f"/energipays_{self._device_id}_{entity.slug}/config")
                await self._enqueue(topic, json.dumps(
                    entity.discovery_payload(self._device_id, self._device_info)), retain=True)
                self._ext_discovered.add(entity.slug)
            value = entity.format_value(self._latest_ext[entity.stat_key])
            if value is not None:
                await self._enqueue(entity.state_topic(self._device_id), value, retain=True)

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

        if self._read_only:
            log.warning("READ_ONLY: blocked MQTT cmd %s = %r", slug, payload)
            await self._publish_cmd_result(
                f"{slug} → {payload!r}: BLOCKED (bridge is read-only)"
            )
            return

        try:
            if slug == "power_diverter_sw":
                val = 1 if payload == "ON" else 0
                res = await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, status=val
                )
                if await self._report(f"Power diverter → {'ON' if val else 'OFF'}", res):
                    await self._enqueue(
                        ENTITY_BY_SLUG["power_diverter_sw"].state_topic(self._device_id),
                        payload, retain=True
                    )

            elif slug == "heater_sw":
                val = 1 if payload == "ON" else 0
                res = await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, heaterStatus=val
                )
                if await self._report(f"Immersion heater → {'ON' if val else 'OFF'}", res):
                    await self._enqueue(
                        ENTITY_BY_SLUG["heater_sw"].state_topic(self._device_id),
                        payload, retain=True
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
                res = await asyncio.to_thread(
                    self._ep_client.set_device_status,
                    self._device_id, **{api_field: val}
                )
                if await self._report(f"{ENTITY_BY_SLUG[slug].name} → {'ON' if val else 'OFF'}", res):
                    await self._enqueue(
                        ENTITY_BY_SLUG[slug].state_topic(self._device_id),
                        payload, retain=True
                    )

            elif slug == "boost_active":
                if payload == "On":
                    period = DURATIONS.get(self._boost_duration_sel, DURATIONS[DEFAULT_DURATION])
                    res = await asyncio.to_thread(
                        self._ep_client.boost_device,
                        self._device_id, self._data_server, period
                    )
                    if await self._report(f"Boost started: {self._boost_duration_sel} (period={period})", res):
                        await self._enqueue(
                            ENTITY_BY_SLUG["boost_active"].state_topic(self._device_id),
                            "On", retain=True
                        )
                elif payload == "Off":
                    res = await asyncio.to_thread(
                        self._ep_client.cancel_boost,
                        self._device_id, self._data_server
                    )
                    if await self._report("Boost cancelled", res):
                        await self._enqueue(
                            ENTITY_BY_SLUG["boost_active"].state_topic(self._device_id),
                            "Off", retain=True
                        )

            elif slug == "boost_duration":
                if payload in DURATIONS:
                    self._boost_duration_sel = payload
                    await self._enqueue(
                        ENTITY_BY_SLUG["boost_duration"].state_topic(self._device_id),
                        payload, retain=True
                    )
                    # Local-only: no API call needed, no result feedback

            elif slug == "boost_power_sel":
                if payload in BOOST_POWER_OPTIONS:
                    idx = BOOST_POWER_REVERSE[payload]
                    res = await asyncio.to_thread(
                        self._ep_client.set_boost_power,
                        self._device_id, idx
                    )
                    if await self._report(f"Boost Power → {payload} (idx={idx})", res):
                        await self._enqueue(
                            ENTITY_BY_SLUG["boost_power_sel"].state_topic(self._device_id),
                            payload, retain=True
                        )

            elif slug == "boost_start":
                period = DURATIONS.get(self._boost_duration_sel, DURATIONS[DEFAULT_DURATION])
                res = await asyncio.to_thread(
                    self._ep_client.boost_device,
                    self._device_id, self._data_server, period
                )
                await self._report(f"Boost started: {self._boost_duration_sel} (period={period})", res)

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
                # energipays.com body: the device's current status_data with only
                # rule_customer changed. Never forces admin/customer/retailer (#2).
                res = await asyncio.to_thread(
                    self._ep_client.activate_rule,
                    self._device_id, rule_id, "command"
                )
                if await self._report(f"Active rule → {payload!r}", res):
                    # Reflect immediately; confirmed state arrives with the next sample
                    await self._enqueue(
                        ENTITY_BY_SLUG["active_rule"].state_topic(self._device_id),
                        payload, retain=True
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

