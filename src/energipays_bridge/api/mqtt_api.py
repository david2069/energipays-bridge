from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from energipays_bridge.config.settings import MqttSettings
from energipays_bridge.publish.entities import (
    BOOST_POWER_MAP,
    ENERGIPAYS_ENTITIES,
)

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
    s = MqttSettings()
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
