from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from energipays_bridge.publish.entities import (
    BOOST_POWER_MAP,
    ENERGIPAYS_ENTITIES,
)
from ..environment import IS_HA_ADDON
from ..mqtt_lifecycle import reconfigure_mqtt
from ..store.db import get_mqtt_settings, set_mqtt_override

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mqtt", tags=["mqtt"])


class MqttConfigOut(BaseModel):
    enabled: bool
    paused: bool
    host: str
    port: int
    username: str
    tls: bool
    discovery_prefix: str
    connected: bool


@router.get("/config", response_model=MqttConfigOut)
async def get_mqtt_config(request: Request):
    s = await get_mqtt_settings(request.app.state.db)
    pub = getattr(request.app.state, "mqtt_publisher", None)
    return MqttConfigOut(
        enabled=s.enabled,
        paused=pub.paused if pub else False,
        host=s.host,
        port=s.port,
        username=s.username,
        tls=s.tls,
        discovery_prefix=s.discovery_prefix,
        connected=pub.connected if pub else False,
    )


class MqttConfigIn(BaseModel):
    enabled: bool
    host: str
    port: int = 1883
    username: str = ""
    password: str | None = None   # None = leave the stored password unchanged
    tls: bool = False


@router.put("/config")
async def put_mqtt_config(body: MqttConfigIn, request: Request) -> dict:
    """Persist MQTT settings (DB override) and apply them immediately.

    This is the single choke point both the setup wizard's MQTT step and
    the Settings → MQTT Discovery card call — no restart required.
    """
    db = request.app.state.db
    db_overrides = {
        "enabled": body.enabled,
        "host": body.host,
        "port": body.port,
        "username": body.username,
        "tls": body.tls,
    }
    if body.password is not None:
        db_overrides["password"] = body.password
    for field, value in db_overrides.items():
        await set_mqtt_override(db, field, value)
    await reconfigure_mqtt(request.app)
    log.info("settings: MQTT config updated (enabled=%s host=%s:%s)",
              body.enabled, body.host, body.port)
    return {"ok": True}


def _probe_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/discover")
async def discover_broker(request: Request) -> dict:
    """Find a usable MQTT broker without the user typing anything.

    HA add-on: query the Supervisor's services API — this returns the
    Mosquitto add-on's host/port AND credentials, no guessing needed.
    Docker/dev: probe a short list of common local addresses (no
    credentials — those brokers are typically open or use env-supplied
    creds the user still has to enter).
    """
    if IS_HA_ADDON:
        import os
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return {"found": False, "error": "SUPERVISOR_TOKEN not set — is hassio_api enabled for this add-on?"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "http://supervisor/services/mqtt",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                data = r.json().get("data") or {}
            if not data:
                return {"found": False, "error": "No MQTT service registered — install the Mosquitto broker add-on"}
            return {
                "found": True,
                "host": data.get("host", ""),
                "port": data.get("port", 1883),
                "username": data.get("username", ""),
                "password": data.get("password", ""),
                "tls": bool(data.get("ssl", False)),
                "source": "supervisor",
            }
        except Exception as exc:
            log.warning("mqtt discover: Supervisor query failed — %s", exc)
            return {"found": False, "error": f"Supervisor query failed: {exc}"}

    candidates = [
        ("core-mosquitto", 1883),
        ("host.docker.internal", 1883),
        ("host.docker.internal", 1886),
        ("localhost", 1883),
    ]
    for host, port in candidates:
        if await asyncio.to_thread(_probe_tcp, host, port):
            return {"found": True, "host": host, "port": port,
                    "username": "", "password": "", "tls": False, "source": "probe"}
    return {"found": False, "error": "No broker found at common local addresses — enter details manually"}


class MqttTestBody(BaseModel):
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False


def _test_connect(host: str, port: int, username: str, password: str, tls: bool) -> tuple[bool, str]:
    import time
    import paho.mqtt.client as mqtt

    state = {"connected": False, "error": ""}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="energipays-bridge-test")
    if username:
        client.username_pw_set(username, password or None)
    if tls:
        client.tls_set()

    def on_connect(c, userdata, flags, rc, props=None):
        state["connected"] = rc == 0
        if rc != 0:
            state["error"] = f"Broker refused connection (rc={rc})"

    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=5)
        client.loop_start()
        deadline = time.monotonic() + 5
        while not state["connected"] and not state["error"] and time.monotonic() < deadline:
            time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
    except Exception as exc:
        return False, str(exc)
    if not state["connected"] and not state["error"]:
        state["error"] = "Timed out waiting for broker response"
    return state["connected"], state["error"]


@router.post("/test")
async def test_broker(body: MqttTestBody) -> dict:
    """Real connect attempt against caller-supplied settings. Test-only — never persists."""
    ok, error = await asyncio.to_thread(
        _test_connect, body.host, body.port, body.username, body.password, body.tls
    )
    return {"ok": ok, "error": None if ok else error}


@router.post("/republish")
async def republish(request: Request):
    pub = getattr(request.app.state, "mqtt_publisher", None)
    if not pub:
        return {"ok": False, "detail": "MQTT publisher not running (MQTT_ENABLED=false)"}
    await pub.republish()
    return {"ok": True, "detail": "Discovery payloads re-queued"}


@router.post("/unpublish")
async def unpublish(request: Request):
    pub = getattr(request.app.state, "mqtt_publisher", None)
    if not pub:
        return {"ok": False, "detail": "MQTT publisher not running"}
    await pub.unpublish()
    return {"ok": True, "detail": "Discovery payloads cleared from HA"}


@router.post("/toggle-pause")
async def toggle_pause(request: Request):
    pub = getattr(request.app.state, "mqtt_publisher", None)
    if not pub:
        return {"ok": False, "paused": True, "detail": "MQTT publisher not running"}
    pub.paused = not pub.paused
    db = request.app.state.db
    from ..store.db import set_config  # noqa: PLC0415
    await set_config(db, "mqtt_paused", "1" if pub.paused else "0")
    return {"ok": True, "paused": pub.paused}


@router.get("/entities")
async def list_entities(request: Request):
    """Return all MQTT entities with their current values from latest points."""
    pts: dict = {}
    poller = getattr(request.app.state, "poller", None)
    if poller:
        pts = getattr(poller, "_latest_points", {}) or {}
    # Fallback: grab from app's _latest_points store
    if not pts:
        pts = getattr(request.app.state, "latest_points", {}) or {}

    pub = getattr(request.app.state, "mqtt_publisher", None)
    rules: list = pub._rules if pub else []
    rule_id_to_name = {r["id"]: r["name"] for r in rules if r.get("name")}

    result = []
    for entity in ENERGIPAYS_ENTITIES:
        raw = pts.get(entity.stat_key) if entity.stat_key else None

        # Formatted display value
        if entity.slug == "boost_power_sel":
            idx = pts.get("dev.boost_power")
            display = BOOST_POWER_MAP.get(int(idx)) if idx else None
        elif entity.slug == "active_rule":
            rid = pts.get("active_rule_id")
            display = rule_id_to_name.get(rid, rid) if rid else None
        elif entity.slug == "boost_duration":
            display = pub._boost_duration_sel if pub else "1 hour"
        elif entity.slug == "last_cmd_result":
            display = pub._last_cmd_result if pub else ""
        elif entity.slug == "last_cmd_ts":
            display = pub._last_cmd_ts if pub else ""
        elif entity.stat_key:
            display = entity.format_value(raw)
        else:
            display = None

        dev_id = pub._device_id if pub else None
        result.append({
            "slug": entity.slug,
            "name": entity.name,
            "ha_type": entity.ha_type,
            "writable": entity.writable,
            "diagnostic": entity.diagnostic,
            "stat_key": entity.stat_key,
            "value": display,
            "state_topic": entity.state_topic(dev_id) if dev_id else None,
            "command_topic": entity.command_topic(dev_id) if (dev_id and entity.writable) else None,
            "options": entity.options if entity.ha_type == "select" else None,
            "unit": entity.unit or None,
            "icon": entity.icon or None,
        })
    return result
