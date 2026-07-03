"""Runtime-reconfigurable MQTT publisher lifecycle.

Extracted out of main.py's lifespan so MQTT can be (re)started at any time —
not just once at boot. This is what makes the setup wizard's MQTT step and
the Settings card's MQTT card able to actually do something: before this,
MqttSettings() was read from env exactly once during FastAPI startup and
there was no code path to reconfigure or enable MQTT afterwards.
"""
from __future__ import annotations

import asyncio
import logging

from .publish.mqtt_publisher import MqttPublisher
from .store.db import get_config, get_mqtt_settings

log = logging.getLogger(__name__)


async def reconfigure_mqtt(app) -> None:
    """(Re)build the MQTT publisher from currently resolved settings.

    Stops and fully unsubscribes any existing publisher, then starts a fresh
    one if MQTT is enabled. Safe to call repeatedly (startup, or any time
    settings change via the API) — a no-op churn if nothing actually changed.
    """
    db = app.state.db
    bridge_settings = app.state.settings
    bus = app.state.bus

    old_pub = getattr(app.state, "mqtt_publisher", None)
    if old_pub is not None:
        await old_pub.stop()
        removed = bus.unsubscribe_type(MqttPublisher)
        log.info("MQTT: stopped previous publisher (%d bus subscription(s) removed)", removed)
        app.state.mqtt_publisher = None

    mqtt_settings = await get_mqtt_settings(db)
    if not mqtt_settings.enabled:
        log.info("MQTT disabled")
        return

    pub = MqttPublisher(
        host=mqtt_settings.host,
        port=mqtt_settings.port,
        username=mqtt_settings.username or None,
        password=mqtt_settings.password or None,
        tls=mqtt_settings.tls,
        discovery_prefix=mqtt_settings.discovery_prefix,
        read_only=bridge_settings.read_only,
    )
    await pub.start()
    if await get_config(db, "mqtt_paused", "0") == "1":
        pub.paused = True
        log.info("MQTT publisher paused (runtime toggle)")
    bus.subscribe(pub.queue_sample)

    # Wire command dispatch + rule names if the Energipays client is already live.
    ep_client = getattr(app.state, "_mqtt_client", None)
    ep_device_id = getattr(app.state, "_mqtt_device_id", None)
    ep_data_server = getattr(app.state, "_mqtt_data_server", "")
    if ep_client and ep_device_id:
        pub.set_client(ep_client, ep_device_id, ep_data_server)

    if ep_client:
        try:
            rules_resp = await asyncio.to_thread(ep_client.rules)
            rules = rules_resp if isinstance(rules_resp, list) else rules_resp.get("data", [])
            await pub.set_rules(rules)
            log.info("MQTT: loaded %d rules for active_rule select", len(rules))
        except Exception as exc:
            log.warning("MQTT: failed to load rules: %s", exc)

    app.state.mqtt_publisher = pub
    log.info("MQTT publisher (re)configured → %s:%s", mqtt_settings.host, mqtt_settings.port)
