"""IntegrationRegistry — manages active pollers, persists config in SQLite."""
from __future__ import annotations

import json
import logging
import time
import uuid

import aiosqlite

from ..sample import SampleBus
from .models import FieldMapping, IntegrationOut
from .secret_store import SECRET_FIELDS, is_encrypted, merge, redact, scrub, seal, unseal
from .rest_poller import RestPoller
from .modbus_poller import ModbusPoller
from .ha_ws_poller import HaWsPoller
from .mqtt_subscriber import MqttSubscriber

log = logging.getLogger(__name__)


class IntegrationRegistry:
    def __init__(self, db: aiosqlite.Connection, bus: SampleBus, key: bytes) -> None:
        self.db = db
        self.bus = bus
        self._key = key                          # machine key for secrets at rest (#17)
        self._pollers: dict[str, object] = {}   # id → poller instance
        self.latest: dict[str, object] = {}      # merged ext.* points
        self._keys: dict[str, set[str]] = {}     # id → ext.* keys it produced (#10)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start_all(self) -> None:
        await self._seal_plaintext_rows()
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
             json.dumps(seal(self._key, data.get("config", {}))),
             json.dumps([m if isinstance(m, dict) else m.model_dump() for m in data.get("mappings", [])]),
             1 if data.get("enabled", True) else 0,
             now),
        )
        await self.db.commit()
        row = await self._fetch_one(row_id)
        if row and row["enabled"]:
            await self._start_one(row)
        return self._enrich(row) if row else None

    async def update(self, row_id: str, data: dict) -> dict | None:
        # A body without "enabled" keeps the stored state; it used to re-enable a
        # disabled integration (#10).
        current = await self._fetch_one(row_id)
        if "enabled" not in data or data.get("enabled") is None:
            data = {**data, "enabled": bool(current and current["enabled"])}
        # The API never sends secrets back, so a blank one keeps what is stored (#17).
        config = merge(current["config"] if current else {}, data.get("config", {}))
        await self._stop_one(row_id)
        await self.db.execute(
            """UPDATE integrations SET name=?, type=?, protocol=?, config=?, mappings=?, enabled=?
               WHERE id=?""",
            (data["name"], data["type"], data["protocol"],
             json.dumps(seal(self._key, config)),
             json.dumps([m if isinstance(m, dict) else m.model_dump() for m in data.get("mappings", [])]),
             1 if data.get("enabled", True) else 0,
             row_id),
        )
        await self.db.commit()
        row = await self._fetch_one(row_id)
        if row and row["enabled"]:
            await self._start_one(row)
        return self._enrich(row) if row else None

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
        return self._enrich(row) if row else None

    async def list_all(self) -> list[dict]:
        rows = await self._fetch_all()
        return [self._enrich(r) for r in rows]

    async def get(self, row_id: str) -> dict | None:
        row = await self._fetch_one(row_id)
        return self._enrich(row) if row else None

    async def get_with_secrets(self, row_id: str) -> dict | None:
        """Row with secrets decrypted — for in-process use (Test), never for a response."""
        row = await self._fetch_one(row_id)
        if row is None:
            return None
        return {**row, "config": unseal(self._key, row["config"])}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _enrich(self, row: dict) -> dict:
        poller = self._pollers.get(row["id"])
        status = getattr(poller, "status", "disabled" if not row["enabled"] else "unknown")
        last_error = getattr(poller, "last_error", "")
        if last_error:
            last_error = scrub(last_error, unseal(self._key, row["config"]))
        config, secrets_set = redact(row["config"])
        return {**row, "config": config, "secrets_set": secrets_set,
                "status": status, "last_error": last_error}

    async def _seal_plaintext_rows(self) -> None:
        """Encrypt secrets saved in plain text before #17 (runs once per row)."""
        for row in await self._fetch_all():
            cfg = row["config"]
            if any(cfg.get(f) and not is_encrypted(cfg.get(f)) for f in SECRET_FIELDS):
                await self.db.execute("UPDATE integrations SET config=? WHERE id=?",
                                      (json.dumps(seal(self._key, cfg)), row["id"]))
                log.info("integrations: encrypted stored secrets of %r", row["name"])
        await self.db.commit()

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
        rid = row["id"]

        async def on_sample(sample, rid=rid):
            await self._on_sample(rid, sample)
        live = {**row, "config": unseal(self._key, row["config"])}
        poller = _build_poller(live, self.bus, on_sample)
        if poller is None:
            return
        self._pollers[row["id"]] = poller
        await poller.start()

    async def _stop_one(self, row_id: str) -> None:
        poller = self._pollers.pop(row_id, None)
        if poller:
            await _stop_poller(poller)
        # A stopped integration's last values must not keep being served as live
        # (#10): drop the keys only it produced.
        keys = self._keys.pop(row_id, set())
        still_live = set().union(*self._keys.values()) if self._keys else set()
        for k in keys - still_live:
            self.latest.pop(k, None)

    async def _on_sample(self, row_id: str, sample) -> None:
        self._keys.setdefault(row_id, set()).update(sample.points)
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
