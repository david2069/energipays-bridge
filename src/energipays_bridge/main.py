"""
energipays-bridge — FastAPI application.

Startup sequence (lifespan):
  1. Load settings (env vars / .env file)
  2. Init SQLite DB + run migrations
  3. Load safe_mode from DB
  4. Authenticate EnergipaysClient
  5. Auto-discover device_id if not configured
  6. Start EnergipaysPoller → SampleBus → MetricsRecorder
  7. Start metrics archival background loop
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import admin, cloud_stats, devices, integrations as integrations_api, metrics, mqtt_api, notifications as notifications_api, points, solar, ui, weather_nem
from .api import setup as setup_api
from .api.admin import install_log_handler, set_log_db
from .api.http_metrics import HttpMetrics, attach_metrics_hook
from .api import http_metrics as http_metrics_api
from .config.settings import BridgeSettings, MqttSettings
from .poller import EnergipaysPoller
from .publish.mqtt_publisher import MqttPublisher
from .sample import SampleBus
from .integrations.registry import IntegrationRegistry
from .store.credentials import load_credentials
from .store.db import get_config, init_db
from .store.metrics import MetricsRecorder, archive_old_metrics, purge_old_archive

log = logging.getLogger(__name__)
_STATIC_DIR = pathlib.Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Settings ──────────────────────────────────────────────────────────
    settings = BridgeSettings()
    settings.data_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    install_log_handler()
    from .environment import RUNTIME
    log.info("energipays-bridge starting [%s]", RUNTIME)

    # ── 2. DB ─────────────────────────────────────────────────────────────────
    db = await init_db(settings.db_path)
    app.state.db = db
    set_log_db(db)
    app.state.settings = settings

    # ── 3. Safe mode ──────────────────────────────────────────────────────────
    safe_mode_val = await get_config(db, "safe_mode", "1")
    app.state.safe_mode = safe_mode_val == "1"

    # ── 4. Energipays credentials ─────────────────────────────────────────────
    try:
        from energipays import EnergipaysClient
    except ImportError:
        log.error("energipays package not found — install with: pip install -e ../energipays-client")
        raise

    if settings.energipays_key:
        import energipays as ep
        ep.set_key(settings.energipays_key)

    email, password = await load_credentials(db, settings.data_path)
    app.state.client = None
    app.state.device_id = None
    app.state.data_server = "https://data-au-1.energipays.com"

    # ── 5. SampleBus (always created so setup endpoint can subscribe later) ───
    bus = SampleBus()
    app.state.bus = bus
    app.state.latest_points: dict = {}
    app.state.latest_ts: float = 0.0
    app.state.latest_quality: str = "unknown"

    async def _store_latest(sample):
        # Only update from the main Energipays device — integration pollers
        # publish ext.* samples under their own device_id; those are merged
        # separately via integration_registry.latest in points.py
        if sample.device_id != getattr(app.state, "device_id", None):
            return
        app.state.latest_points = sample.points
        app.state.latest_ts = sample.ts
        app.state.latest_quality = sample.quality

    bus.subscribe(_store_latest)

    # ── 6. Login + device discovery (skipped if no credentials) ──────────────
    poller: EnergipaysPoller | None = None
    device_id: str = settings.energipays_device_id
    data_server: str = "https://data-au-1.energipays.com"
    if email and password:
        try:
            client = EnergipaysClient(email=email, password=password, auto_login=False)
            http_metrics = HttpMetrics()
            attach_metrics_hook(client, http_metrics)
            app.state.http_metrics = http_metrics
            log.info("Logging in to Energipays...")
            await asyncio.to_thread(client.login)
            log.info("Login OK")
            app.state.client = client

            if not device_id:
                try:
                    resp = await asyncio.to_thread(client.devices)
                    devs = resp if isinstance(resp, list) else resp.get("data", [])
                    if devs:
                        device_id = devs[0]["id"]
                        data_server = devs[0].get("server", data_server)
                        log.info("Auto-discovered device: %s", device_id)
                except Exception as exc:
                    log.warning("Device discovery failed: %s", exc)
            app.state.device_id = device_id
            app.state.data_server = data_server

            metrics_val = await get_config(db, "metrics_enabled", "1")
            app.state.metrics_enabled = metrics_val == "1"
            if app.state.metrics_enabled:
                recorder = MetricsRecorder(db)
                bus.subscribe(recorder)
                log.info("MetricsRecorder enabled — writing to local DB")
            else:
                log.info("MetricsRecorder disabled — bridge mode (no local metrics)")

            if device_id:
                poller = EnergipaysPoller(
                    client, bus, device_id, data_server,
                    poll_interval=settings.poll_interval,
                )
                await poller.start()
            else:
                log.warning("No device_id — poller not started")

            # Wire client into MQTT publisher for command dispatch (done after login)
            if device_id:
                app.state._mqtt_client = client
                app.state._mqtt_device_id = device_id
                app.state._mqtt_data_server = data_server
        except Exception as exc:
            log.warning("Startup login failed: %s — waiting for credentials via UI", exc)
    else:
        log.warning("No credentials found — open http://localhost:%d to configure", settings.admin_port)

    app.state.poller = poller

    # ── 7. MQTT publisher ─────────────────────────────────────────────────────
    mqtt_settings = MqttSettings()
    mqtt_publisher: MqttPublisher | None = None
    if mqtt_settings.enabled:
        mqtt_publisher = MqttPublisher(
            host=mqtt_settings.host,
            port=mqtt_settings.port,
            username=mqtt_settings.username or None,
            password=mqtt_settings.password or None,
            tls=mqtt_settings.tls,
            discovery_prefix=mqtt_settings.discovery_prefix,
        )
        await mqtt_publisher.start()
        if await get_config(db, "mqtt_paused", "0") == "1":
            mqtt_publisher.paused = True
            log.info("MQTT publisher paused (runtime toggle)")
        bus.subscribe(mqtt_publisher.queue_sample)
        log.info("MQTT publisher enabled → %s:%s", mqtt_settings.host, mqtt_settings.port)

        # Wire command dispatch if client is available
        ep_client = getattr(app.state, "_mqtt_client", None)
        ep_device_id = getattr(app.state, "_mqtt_device_id", None)
        ep_data_server = getattr(app.state, "_mqtt_data_server", "")
        if ep_client and ep_device_id:
            mqtt_publisher.set_client(ep_client, ep_device_id, ep_data_server)

        # Fetch rules for active_rule select options
        if ep_client:
            try:
                rules_resp = await asyncio.to_thread(ep_client.rules)
                rules = rules_resp if isinstance(rules_resp, list) else rules_resp.get("data", [])
                await mqtt_publisher.set_rules(rules)
                log.info("MQTT: loaded %d rules for active_rule select", len(rules))
            except Exception as exc:
                log.warning("MQTT: failed to load rules: %s", exc)
    else:
        log.info("MQTT disabled — set MQTT_ENABLED=true to enable")
    app.state.mqtt_publisher = mqtt_publisher

    # ── 8. Integration registry ───────────────────────────────────────────────
    integration_registry = IntegrationRegistry(db, bus)
    await integration_registry.start_all()
    app.state.integration_registry = integration_registry
    log.info("Integration registry started")

    # ── 8b. Notification trigger ──────────────────────────────────────────────
    from .notifications.trigger import NotificationTrigger
    notif_trigger = NotificationTrigger(db, device_id or "")
    bus.subscribe(notif_trigger)
    log.info("NotificationTrigger registered")

    # ── 9. Metrics archival loop ──────────────────────────────────────────────
    async def _archival_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                await archive_old_metrics(db, settings.raw_age_days * 86400)
                await purge_old_archive(db, settings.retention_days * 86400)
            except Exception as exc:
                log.warning("Archival loop error: %s", exc)

    archival_task = asyncio.create_task(_archival_loop(), name="metrics-archival")

    # ── 10. Log cleanup loop ──────────────────────────────────────────────────
    async def _cleanup_logs():
        while True:
            await asyncio.sleep(86400)
            cutoff = time.time() - 7 * 86400
            await db.execute("DELETE FROM app_logs WHERE ts < ?", (cutoff,))
            await db.commit()

    cleanup_task = asyncio.create_task(_cleanup_logs(), name="log-cleanup")

    log.info("energipays-bridge ready on port %d", settings.admin_port)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    archival_task.cancel()
    cleanup_task.cancel()
    await integration_registry.stop_all()
    if poller:
        await poller.stop()
    if mqtt_publisher:
        await mqtt_publisher.stop()
    await db.close()
    log.info("energipays-bridge stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Energipays Bridge", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(ui.router)
    app.include_router(setup_api.router)
    app.include_router(integrations_api.router)
    app.include_router(points.router)
    app.include_router(metrics.router)
    app.include_router(devices.router)
    app.include_router(admin.router)
    app.include_router(mqtt_api.router)
    app.include_router(http_metrics_api.router)
    app.include_router(cloud_stats.router)
    app.include_router(weather_nem.router)
    app.include_router(solar.router)
    app.include_router(notifications_api.router)
    return app


app = create_app()
