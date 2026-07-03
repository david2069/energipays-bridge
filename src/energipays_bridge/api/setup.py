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


class AesKeyBody(BaseModel):
    key: str


async def _test_login(email: str, password: str) -> dict:
    """Try to log in; return {"ok": True, "user": {...}} or {"ok": False, "error": "..."}."""
    log.info("auth: attempting login for %s", email)
    try:
        from energipays import EnergipaysClient
        client = EnergipaysClient(email=email, password=password, auto_login=False)
        # _ensure_key uses the validated path: makes one API call with credentials
        # to get an encrypted response, then finds the key that decrypts it.
        await asyncio.to_thread(client._ensure_key)
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
    from ..environment import RUNTIME
    db = request.app.state.db
    data_dir = request.app.state.settings.data_path
    configured = await has_credentials(db, data_dir)
    return {
        "configured": configured,
        "poller_running": bool(request.app.state.poller
                               and request.app.state.poller.connected),
        "runtime": RUNTIME,  # "ha_addon" | "docker" | "dev"
    }


@router.post("/api/setup/set-key")
async def setup_set_key(body: AesKeyBody, request: Request) -> dict:
    """Manually set the AES key when automatic extraction fails."""
    import base64
    import json
    key = body.key.strip()
    try:
        raw = base64.b64decode(key)
        if len(raw) != 32:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"Key must decode to 32 bytes, got {len(raw)}"})
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid base64 key"})
    try:
        import energipays as ep
        ep.set_key(key)
        # Persist to cache so it survives restarts
        import os, pathlib
        data_dir = pathlib.Path(os.environ.get("DATA_DIR", str(request.app.state.settings.data_path)))
        cache = data_dir / ".key_cache.json"
        cache.write_text(json.dumps({"key": key}))
        log.info("setup: AES key set manually and cached to %s", cache)
        return {"ok": True}
    except Exception as exc:
        log.error("setup: failed to set AES key — %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


def _run_key_diagnostics(data_dir: str) -> dict:
    """Step-by-step AES key extraction probe, run from inside the container.

    Each step records ok/detail/elapsed; if extraction yields exactly one
    unique Base64.parse candidate, the key is installed + cached on the spot,
    so a successful diagnostics run doubles as the repair.
    """
    import base64
    import json as _json
    import pathlib
    import socket
    import time

    import requests

    steps: list[dict] = []

    def step(name: str, fn) -> tuple[bool, object]:
        t0 = time.monotonic()
        try:
            detail = fn()
            steps.append({"name": name, "ok": True, "detail": str(detail),
                          "ms": int((time.monotonic() - t0) * 1000)})
            return True, detail
        except Exception as exc:
            steps.append({"name": name, "ok": False,
                          "detail": f"{type(exc).__name__}: {exc}",
                          "ms": int((time.monotonic() - t0) * 1000)})
            return False, None

    def _dns(host: str) -> str:
        addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443)})
        return ", ".join(addrs)

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (energipays-bridge key diagnostics)"

    step("DNS resolve energipays.com", lambda: _dns("energipays.com"))
    step("DNS resolve data-au-1.energipays.com", lambda: _dns("data-au-1.energipays.com"))

    def _front() -> str:
        r = session.get("https://energipays.com/", timeout=20)
        return f"HTTP {r.status_code}, {len(r.content)} bytes"
    step("Fetch https://energipays.com/", _front)

    def _chunks() -> list[str]:
        from extract_key import discover_chunk_urls
        urls = discover_chunk_urls("https://energipays.com", session)
        if not urls:
            raise RuntimeError("no JS chunk URLs found (asset-manifest.json + index.html scrape both empty)")
        return urls
    ok, chunk_urls = step("Discover JS chunk URLs", _chunks)
    if ok:
        steps[-1]["detail"] = f"{len(chunk_urls)} chunks"

    key_installed = False
    if ok:
        def _scan() -> str:
            from extract_key import context_candidates_from_js
            candidates: list[str] = []
            per_chunk: list[str] = []
            for url in chunk_urls:
                r = session.get(url, timeout=20)
                n = 0
                if r.ok:
                    for c in context_candidates_from_js(r.text):
                        n += 1
                        if c not in candidates:
                            candidates.append(c)
                per_chunk.append(f"{url.rsplit('/', 1)[-1]}: HTTP {r.status_code}, {n} candidate(s)")
            if len(candidates) != 1:
                raise RuntimeError(f"need exactly 1 unique Base64.parse candidate, got {len(candidates)} — " + "; ".join(per_chunk))
            return candidates[0]
        ok, key = step("Scan chunks for Base64.parse key candidates", _scan)
        if ok:
            steps[-1]["detail"] = f"exactly 1 candidate: {key[:4]}…{key[-4:]}"

            def _install() -> str:
                raw = base64.b64decode(key)
                if len(raw) != 32:
                    raise RuntimeError(f"candidate decodes to {len(raw)} bytes, expected 32")
                import energipays as ep
                ep.set_key(key)
                cache = pathlib.Path(data_dir) / ".key_cache.json"
                cache.write_text(_json.dumps({"key": key}))
                return f"key set + cached to {cache}"
            key_installed, _ = step("Install + cache key", _install)

    return {"ok": key_installed, "key_installed": key_installed, "steps": steps}


@router.post("/api/setup/key-diagnostics")
async def setup_key_diagnostics(request: Request) -> dict:
    """Probe every stage of AES key auto-extraction and report each step.

    On full success the extracted key is installed and cached, so this
    endpoint is both the diagnosis and (when the network allows) the fix.
    """
    import os
    data_dir = os.environ.get("DATA_DIR", str(request.app.state.settings.data_path))
    log.info("setup: running AES key diagnostics (data_dir=%s)", data_dir)
    result = await asyncio.to_thread(_run_key_diagnostics, data_dir)
    for s in result["steps"]:
        log.log(logging.INFO if s["ok"] else logging.WARNING,
                "setup: key-diag %s — %s (%sms)",
                "OK " if s["ok"] else "FAIL", s["name"] + ": " + s["detail"], s["ms"])
    return result


class CliBody(BaseModel):
    cmd: str


@router.post("/api/setup/run-cli")
async def setup_run_cli(body: CliBody) -> dict:
    """Run an energipays or energipays-bridge CLI command and return stdout+stderr."""
    import shlex, subprocess, sys, pathlib, shutil
    cmd = body.cmd.strip()
    if not cmd:
        return {"output": ""}
    try:
        args = shlex.split(cmd)
    except ValueError as exc:
        return {"output": f"Parse error: {exc}"}
    allowed = {"energipays", "energipays-bridge"}
    if not args or args[0] not in allowed:
        return {"output": f"Only 'energipays' and 'energipays-bridge' commands are allowed."}

    # Resolve executable: check PATH first, then Scripts/ dir next to sys.executable
    exe_name = args[0]
    exe = shutil.which(exe_name)
    if not exe:
        # venv Scripts or bin directory
        scripts = pathlib.Path(sys.executable).parent
        candidate = scripts / exe_name
        if candidate.exists():
            exe = str(candidate)
    if not exe:
        return {"output": f"'{exe_name}' not found. Try: python -m energipays"}

    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run([exe] + args[1:], capture_output=True, text=True, timeout=30)
        )
        out = (result.stdout or "") + (result.stderr or "")
        return {"output": out.strip() or "(no output)"}
    except subprocess.TimeoutExpired:
        return {"output": "Timed out after 30s"}
    except Exception as exc:
        return {"output": f"Error: {exc}"}


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
