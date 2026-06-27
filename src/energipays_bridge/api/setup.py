"""
Setup / onboarding API endpoints.

POST /api/setup/test   — test credentials without saving
POST /api/setup/save   — test + save credentials + start poller
GET  /api/setup/status — whether credentials are configured
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..store.credentials import has_credentials, load_credentials, save_credentials

log = logging.getLogger(__name__)
router = APIRouter()


class CredentialsBody(BaseModel):
    email: str
    password: str


async def _test_login(email: str, password: str) -> dict:
    """Try to log in; return {"ok": True, "user": {...}} or {"ok": False, "error": "..."}."""
    log.info("auth: attempting login for %s", email)
    try:
        from energipays import EnergipaysClient
        client = EnergipaysClient(email=email, password=password, auto_login=False)
        log.debug("auth: sending CSRF bootstrap + encrypted login POST to energipays.com")
        resp = await asyncio.to_thread(client.login)
        token = resp.get("access_token") or resp.get("token") or resp.get("accessToken")
        if not token:
            msg = resp.get("message") or resp.get("error") or "Login failed — check your credentials"
            log.warning("auth: login rejected for %s — %s", email, msg)
            return {"ok": False, "error": str(msg)}
        log.info("auth: Bearer token acquired (expires in ~300s)")
        log.debug("auth: fetching user profile to confirm identity")
        me = await asyncio.to_thread(client.me)
        name = me.get("name", "")
        confirmed_email = me.get("email", email)
        log.info("auth: login confirmed — user=%s <%s>", name, confirmed_email)
        return {"ok": True, "user": {"name": name, "email": confirmed_email}}
    except Exception as exc:
        log.error("auth: login error for %s — %s", email, exc)
        return {"ok": False, "error": str(exc)}


@router.get("/api/setup/status")
async def setup_status(request: Request) -> dict:
    db = request.app.state.db
    data_dir = request.app.state.settings.data_path
    configured = await has_credentials(db, data_dir)
    return {
        "configured": configured,
        "poller_running": bool(request.app.state.poller
                               and request.app.state.poller.connected),
    }


@router.post("/api/setup/test")
async def setup_test(body: CredentialsBody) -> dict:
    """Test credentials without saving. Returns success/failure + user info."""
    return await _test_login(body.email, body.password)


@router.post("/api/setup/save")
async def setup_save(body: CredentialsBody, request: Request) -> dict:
    """Test credentials, save them if valid, then start the poller."""
    result = await _test_login(body.email, body.password)
    if not result["ok"]:
        return JSONResponse(status_code=400, content=result)

    db = request.app.state.db
    settings = request.app.state.settings
    log.info("setup: login verified — saving encrypted credentials for %s", body.email)
    await save_credentials(db, settings.data_path, body.email, body.password)

    # Start the poller if it isn't running yet
    if not request.app.state.poller:
        log.info("setup: starting poller for %s", body.email)
        await _start_poller(request, body.email, body.password)

    return result


async def _start_poller(request: Request, email: str, password: str) -> None:
    """Instantiate and start the poller after credentials are saved."""
    from energipays import EnergipaysClient
    from ..poller import EnergipaysPoller
    from ..store.metrics import MetricsRecorder

    settings = request.app.state.settings
    log.debug("poller-init: creating EnergipaysClient for %s", email)
    client = EnergipaysClient(email=email, password=password, auto_login=False)
    log.info("poller-init: re-authenticating to obtain fresh Bearer token")
    await asyncio.to_thread(client.login)
    log.info("poller-init: authentication OK")

    # Auto-discover device
    device_id = settings.energipays_device_id
    data_server = "https://data-au-1.energipays.com"
    if not device_id:
        log.info("poller-init: no device_id configured — auto-discovering from account")
        try:
            resp = await asyncio.to_thread(client.devices)
            devs = resp if isinstance(resp, list) else resp.get("data", [])
            if devs:
                device_id = devs[0]["id"]
                data_server = devs[0].get("server", data_server)
                log.info("poller-init: discovered device_id=%s data_server=%s", device_id, data_server)
            else:
                log.warning("poller-init: no devices returned from account")
        except Exception as exc:
            log.warning("poller-init: device discovery failed — %s", exc)

    if not device_id:
        log.warning("poller-init: cannot start poller — no device found")
        return

    request.app.state.device_id = device_id
    request.app.state.data_server = data_server
    request.app.state.client = client

    bus = request.app.state.bus
    recorder = MetricsRecorder(request.app.state.db)
    bus.subscribe(recorder)
    log.debug("poller-init: MetricsRecorder subscribed to SampleBus")

    poller = EnergipaysPoller(
        client, bus, device_id, data_server,
        poll_interval=settings.poll_interval,
    )
    await poller.start()
    request.app.state.poller = poller
    log.info("poller-init: poller running (device=%s poll_interval=%ss)", device_id, settings.poll_interval)
