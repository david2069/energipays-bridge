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

from .api import admin, devices, metrics, points, ui
from .api.admin import install_log_handler
from .config.settings import BridgeSettings
from .poller import EnergipaysPoller
from .sample import SampleBus
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
    log.info("energipays-bridge starting")

    # ── 2. DB ─────────────────────────────────────────────────────────────────
    db = await init_db(settings.db_path)
    app.state.db = db

    # ── 3. Safe mode ──────────────────────────────────────────────────────────
    safe_mode_val = await get_config(db, "safe_mode", "1")
    app.state.safe_mode = safe_mode_val == "1"

    # ── 4. Energipays client ──────────────────────────────────────────────────
    try:
        from energipays import EnergipaysClient
    except ImportError:
        log.error("energipays package not found — install with: pip install -e ../energipays-client")
        raise

    if settings.energipays_key:
        import energipays as ep
        ep.set_key(settings.energipays_key)

    client = EnergipaysClient(
        email=settings.energipays_email,
        password=settings.energipays_password,
        auto_login=False,
    )
    if settings.energipays_email and settings.energipays_password:
        log.info("Logging in to Energipays...")
        await asyncio.to_thread(client.login)
        log.info("Login OK")
    else:
        log.warning("No ENERGIPAYS_EMAIL/PASSWORD set — running unauthenticated")
    app.state.client = client

    # ── 5. Device discovery ───────────────────────────────────────────────────
    device_id = settings.energipays_device_id
    data_server = "https://data-au-1.energipays.com"
    if not device_id and settings.energipays_email:
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

    # ── 6. SampleBus + Poller ─────────────────────────────────────────────────
    bus = SampleBus()
    app.state.latest_points: dict = {}
    app.state.latest_ts: float = 0.0
    app.state.latest_quality: str = "unknown"

    async def _store_latest(sample):
        app.state.latest_points = sample.points
        app.state.latest_ts = sample.ts
        app.state.latest_quality = sample.quality

    bus.subscribe(_store_latest)
    recorder = MetricsRecorder(db)
    bus.subscribe(recorder)

    poller: EnergipaysPoller | None = None
    if device_id:
        poller = EnergipaysPoller(
            client, bus, device_id, data_server,
            poll_interval=settings.poll_interval,
        )
        await poller.start()
    else:
        log.warning("No device_id — poller not started")
    app.state.poller = poller

    # ── 7. Metrics archival loop ──────────────────────────────────────────────
    async def _archival_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                await archive_old_metrics(db, settings.raw_age_days * 86400)
                await purge_old_archive(db, settings.retention_days * 86400)
            except Exception as exc:
                log.warning("Archival loop error: %s", exc)

    archival_task = asyncio.create_task(_archival_loop(), name="metrics-archival")

    log.info("energipays-bridge ready on port %d", settings.admin_port)
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    archival_task.cancel()
    if poller:
        await poller.stop()
    await db.close()
    log.info("energipays-bridge stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Energipays Bridge", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(ui.router)
    app.include_router(points.router)
    app.include_router(metrics.router)
    app.include_router(devices.router)
    app.include_router(admin.router)
    return app


app = create_app()
