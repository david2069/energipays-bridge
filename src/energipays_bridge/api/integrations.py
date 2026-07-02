"""API routes for External Integrations (battery/solar via REST/Modbus TCP/HA WS/MQTT)."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..integrations.models import HaEntitiesRequest, IntegrationIn, ProbeRequest, SunSpecDiscoverRequest
from ..integrations.sunspec_scanner import scan_sunspec
from ..integrations.modbus_poller import _parse_source, read_registers, _REG_COUNT
from ..integrations.rest_poller import RestPoller, _extract_mappings, _dotpath

router = APIRouter(prefix="/api/integrations")
log = logging.getLogger(__name__)


def _registry(request: Request):
    reg = getattr(request.app.state, "integration_registry", None)
    if reg is None:
        raise HTTPException(503, "Integration registry not initialised")
    return reg


@router.get("")
async def list_integrations(request: Request) -> list[dict]:
    return await _registry(request).list_all()


@router.post("")
async def create_integration(request: Request, body: IntegrationIn) -> dict:
    reg = _registry(request)
    row = await reg.create(body.model_dump())
    return row


@router.put("/{row_id}")
async def update_integration(request: Request, row_id: str, body: IntegrationIn) -> dict:
    reg = _registry(request)
    row = await reg.update(row_id, body.model_dump())
    if row is None:
        raise HTTPException(404, "Integration not found")
    return row


@router.delete("/{row_id}")
async def delete_integration(request: Request, row_id: str) -> dict:
    reg = _registry(request)
    ok = await reg.delete(row_id)
    if not ok:
        raise HTTPException(404, "Integration not found")
    return {"ok": True}


@router.post("/{row_id}/enable")
async def toggle_enable(request: Request, row_id: str, body: dict) -> dict:
    reg = _registry(request)
    enabled = bool(body.get("enabled", True))
    row = await reg.set_enabled(row_id, enabled)
    if row is None:
        raise HTTPException(404, "Integration not found")
    return row


@router.post("/{row_id}/test")
async def test_integration(request: Request, row_id: str) -> dict:
    reg = _registry(request)
    row = await reg.get(row_id)
    if row is None:
        raise HTTPException(404, "Integration not found")

    from ..integrations.models import FieldMapping
    mappings = [FieldMapping(**m) for m in row["mappings"]]
    cfg = row["config"]
    proto = row["protocol"]

    try:
        if proto == "rest":
            url = cfg.get("base_url", "").rstrip("/") + cfg.get("endpoint", "/api/points/latest")
            headers = {}
            if cfg.get("auth_type") == "bearer":
                headers["Authorization"] = f"Bearer {cfg.get('auth_token', '')}"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                data = r.json()
            readings = []
            for m in mappings:
                raw = _dotpath(data, m.source)
                scaled = float(raw) * m.scale if raw is not None else None
                readings.append({"source": m.source, "target_metric": m.target_metric,
                                  "raw": raw, "scaled": scaled})
            return {"ok": True, "readings": readings}

        if proto == "modbus_tcp":
            readings = []
            for m in mappings:
                fc, address, reg_type, scale = _parse_source(m.source)
                count = _REG_COUNT[reg_type]
                values = await read_registers(cfg["host"], int(cfg.get("port", 502)),
                                              int(cfg.get("unit_id", 1)), fc, address, count, reg_type)
                raw = values[0] if values else None
                scaled = raw * scale if raw is not None else None
                readings.append({"source": m.source, "target_metric": m.target_metric,
                                  "raw": raw, "scaled": scaled})
            return {"ok": True, "readings": readings}

        if proto == "ha_ws":
            # Fetch current states for mapped entity IDs
            base = cfg.get("url", "").replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
            token = cfg.get("token", "")
            entity_ids = {m.source for m in mappings}
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/states", headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                states = {s["entity_id"]: s for s in r.json()}
            readings = []
            for m in mappings:
                state_obj = states.get(m.source, {})
                raw = state_obj.get("state")
                try:
                    scaled = float(raw) * m.scale
                except (TypeError, ValueError):
                    scaled = raw
                readings.append({"source": m.source, "target_metric": m.target_metric,
                                  "raw": raw, "scaled": scaled})
            return {"ok": True, "readings": readings}

        if proto in ("mqtt", "sunspec_tcp"):
            return {"ok": False, "error": f"{proto.upper()} test not supported via this endpoint — use Probe or Discover instead"}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": f"Unknown protocol: {proto}"}


@router.post("/probe")
async def probe_registers(body: ProbeRequest) -> dict:
    """Ad-hoc Modbus TCP register probe — no saved integration needed."""
    try:
        count = body.count * _REG_COUNT[body.type]
        values = await read_registers(
            body.host, body.port, body.unit_id,
            body.fc, body.address, count, body.type,
        )
        return {"ok": True, "registers": values[:body.count]}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@router.post("/sunspec-discover")
async def sunspec_discover(body: SunSpecDiscoverRequest) -> dict:
    """Scan a Modbus TCP device for SunSpec models and return auto-mappings."""
    result = await scan_sunspec(body.host, body.port, body.unit_id, body.sunspec_start)
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result


@router.post("/ha-entities")
async def ha_entities(body: HaEntitiesRequest) -> dict:
    """Fetch all HA entity states for the entity browser."""
    base = body.url.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}/api/states",
                headers={"Authorization": f"Bearer {body.token}"},
            )
            r.raise_for_status()
            all_states = r.json()
    except Exception as exc:
        raise HTTPException(400, str(exc))

    entities = []
    for s in all_states:
        entity_id = s.get("entity_id", "")
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        if body.domain and domain != body.domain:
            continue
        name = (s.get("attributes") or {}).get("friendly_name", entity_id)
        unit = (s.get("attributes") or {}).get("unit_of_measurement", "")
        search = body.search.lower()
        if search and search not in entity_id.lower() and search not in name.lower():
            continue
        entities.append({
            "entity_id": entity_id,
            "name": name,
            "domain": domain,
            "state": s.get("state", ""),
            "unit": unit,
        })

    entities.sort(key=lambda e: e["entity_id"])
    total = len(entities)
    start = (body.page - 1) * body.page_size
    page_items = entities[start:start + body.page_size]
    domains = sorted({e["domain"] for e in entities if e["domain"]})

    return {
        "total": total,
        "page": body.page,
        "page_size": body.page_size,
        "pages": max(1, (total + body.page_size - 1) // body.page_size),
        "domains": domains,
        "entities": page_items,
    }
