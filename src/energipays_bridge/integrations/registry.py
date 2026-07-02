"""IntegrationRegistry — manages active pollers, persists config in SQLite."""
from __future__ import annotations

import json
import logging
import time
import uuid

import aiosqlite

from ..sample import SampleBus
from .models import FieldMapping, IntegrationOut
from .rest_poller import RestPoller
from .modbus_poller import ModbusPoller
from .ha_ws_poller import HaWsPoller
from .mqtt_subscriber import MqttSubscriber

log = logging.getLogger(__name__)


class IntegrationRegistry:
    def __init__(self, db: aiosqlite.Connection, bus: SampleBus) -> None:
        self.db = db
        self.bus = bus
        self._pollers: dict[str, object] = {}   # id → poller instance
        self.latest: dict[str, object] = {}      # merged ext.* points

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start_all(self) -> None:
        rows = await self._fetch_all()
        for row in rows:
            if row["enabled"]:
                await self._start_one(row)

    async def stop_all(self) -> None:
        for poller in list(self._pollers.values()):
            await _stop_poller(poller)
        self._pollers.clear()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(self, data: dict) -> dict:
        row_id = str(uuid.uuid4())
        now = time.time()
        await self.db.execute(
            """INSERT INTO integrations (id, name, type, protocol, config, mappings, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row_id, data["name"], data["type"], data["protocol"],
             json.dumps(data.get("config", {})),
             json.dumps([m if isinstance(m, dict) else m.model_dump() for m in data.get("mappings", [])]),
             1 if data.get("enabled", True) else 0,
             now),
        )
        await self.db.commit()
        row = await self._fetch_one(row_id)
        if row and row["enabled"]:
            await self._start_one(row)
        return row

    async def update(self, row_id: str, data: dict) -> dict | None:
        await self._stop_one(row_id)
        await self.db.execute(
            """UPDATE integrations SET name=?, type=?, protocol=?, config=?, mappings=?, enabled=?
               WHERE id=?""",
            (data["name"], data["type"], data["protocol"],
             json.dumps(data.get("config", {})),
             json.dumps([m if isinstance(m, dict) else m.model_dump() for m in data.get("mappings", [])]),
             1 if data.get("enabled", True) else 0,
             row_id),
        )
        await self.db.commit()
        row = await self._fetch_one(row_id)
        if row and row["enabled"]:
            await self._start_one(row)
        return row

    async def delete(self, row_id: str) -> bool:
        await self._stop_one(row_id)
        cur = await self.db.execute("DELETE FROM integrations WHERE id=?", (row_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def set_enabled(self, row_id: str, enabled: bool) -> dict | None:
        if enabled:
            await self.db.execute("UPDATE integrations SET enabled=1 WHERE id=?", (row_id,))
        else:
            await self._stop_one(row_id)
            await self.db.execute("UPDATE integrations SET enabled=0 WHERE id=?", (row_id,))
        await self.db.commit()
        row = await self._fetch_one(row_id)
        if row and row["enabled"]:
            await self._start_one(row)
        return row

    async def list_all(self) -> list[dict]:
        rows = await self._fetch_all()
        return [self._enrich(r) for r in rows]

    async def get(self, row_id: str) -> dict | None:
        row = await self._fetch_one(row_id)
        return self._enrich(row) if row else None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _enrich(self, row: dict) -> dict:
        poller = self._pollers.get(row["id"])
        status = getattr(poller, "status", "disabled" if not row["enabled"] else "unknown")
        last_error = getattr(poller, "last_error", "")
        return {**row, "status": status, "last_error": last_error}

    async def _fetch_all(self) -> list[dict]:
        cur = await self.db.execute(
            "SELECT id, name, type, protocol, config, mappings, enabled, created_at FROM integrations ORDER BY created_at"
        )
        cur.row_factory = aiosqlite.Row
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def _fetch_one(self, row_id: str) -> dict | None:
        cur = await self.db.execute(
            "SELECT id, name, type, protocol, config, mappings, enabled, created_at FROM integrations WHERE id=?",
            (row_id,),
        )
        cur.row_factory = aiosqlite.Row
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def _start_one(self, row: dict) -> None:
        await self._stop_one(row["id"])
        poller = _build_poller(row, self.bus, self._on_sample)
        if poller is None:
            return
        self._pollers[row["id"]] = poller
        await poller.start()

    async def _stop_one(self, row_id: str) -> None:
        poller = self._pollers.pop(row_id, None)
        if poller:
            await _stop_poller(poller)

    async def _on_sample(self, sample) -> None:
        self.latest.update(sample.points)


def _row_to_dict(row) -> dict:
    r = dict(row)
    r["config"] = json.loads(r["config"])
    r["mappings"] = json.loads(r["mappings"])
    r["enabled"] = bool(r["enabled"])
    return r


def _build_poller(row: dict, bus: SampleBus, on_sample=None) -> object | None:
    mappings = [FieldMapping(**m) for m in row["mappings"]]
    cfg = row["config"]
    proto = row["protocol"]
    rid = row["id"]
    name = row["name"]

    wrapped_bus = bus
    if on_sample:
        class _WrappedBus:
            def __init__(self, inner, cb):
                self._inner = inner
                self._cb = cb
            def subscribe(self, fn): self._inner.subscribe(fn)
            def unsubscribe_type(self, cls): self._inner.unsubscribe_type(cls)
            async def publish(self, sample):
                await self._cb(sample)
                await self._inner.publish(sample)
        wrapped_bus = _WrappedBus(bus, on_sample)

    if proto == "rest":
        return RestPoller(
            integration_id=rid, name=name, bus=wrapped_bus,
            base_url=cfg.get("base_url", ""),
            endpoint=cfg.get("endpoint", "/api/points/latest"),
            mappings=mappings,
            auth_type=cfg.get("auth_type", "none"),
            auth_token=cfg.get("auth_token", ""),
            poll_interval=int(cfg.get("poll_interval", 30)),
        )
    if proto in ("modbus_tcp", "sunspec_tcp"):
        return ModbusPoller(
            integration_id=rid, name=name, bus=wrapped_bus,
            host=cfg.get("host", ""),
            port=int(cfg.get("port", 502)),
            unit_id=int(cfg.get("unit_id", 1)),
            mappings=mappings,
            poll_interval=int(cfg.get("poll_interval", 10)),
        )
    if proto == "ha_ws":
        return HaWsPoller(
            integration_id=rid, name=name, bus=wrapped_bus,
            url=cfg.get("url", ""),
            token=cfg.get("token", ""),
            mappings=mappings,
        )
    if proto == "mqtt":
        return MqttSubscriber(
            integration_id=rid, name=name, bus=wrapped_bus,
            host=cfg.get("host", ""),
            port=int(cfg.get("port", 1883)),
            username=cfg.get("username", ""),
            password=cfg.get("password", ""),
            mappings=mappings,
        )
    log.warning("Unknown protocol %r for integration %r", proto, name)
    return None


async def _stop_poller(poller) -> None:
    try:
        await poller.stop()
    except Exception as exc:
        log.warning("Error stopping poller: %s", exc)
