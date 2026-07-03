"""SQLite initialisation and schema migrations (forward-only, numbered)."""
from __future__ import annotations

import logging
import pathlib

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATIONS = [
    # v1 — initial schema
    """
    CREATE TABLE IF NOT EXISTS metric_samples (
        device_id TEXT NOT NULL,
        point_id  TEXT NOT NULL,
        ts        REAL NOT NULL,
        value     REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_metric_samples
        ON metric_samples (device_id, point_id, ts);

    CREATE TABLE IF NOT EXISTS metric_samples_archive (
        device_id TEXT NOT NULL,
        point_id  TEXT NOT NULL,
        ts        REAL NOT NULL,
        value     REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_metric_archive
        ON metric_samples_archive (device_id, point_id, ts);

    CREATE TABLE IF NOT EXISTS app_config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    );
    """,
    # v2 — persistent application log storage
    """
    CREATE TABLE IF NOT EXISTS app_logs (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      REAL    NOT NULL,
        level   TEXT    NOT NULL,
        logger  TEXT    NOT NULL,
        msg     TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_app_logs_ts ON app_logs (ts);
    """,
    # v3 — external integrations (battery/solar via REST, Modbus TCP, HA WS, MQTT)
    """
    CREATE TABLE IF NOT EXISTS integrations (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        type       TEXT NOT NULL,
        protocol   TEXT NOT NULL,
        config     TEXT NOT NULL DEFAULT '{}',
        mappings   TEXT NOT NULL DEFAULT '[]',
        enabled    INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL DEFAULT 0
    );
    """,
    # v4 — push notifications: HA instances, companion devices, notification settings
    """
    CREATE TABLE IF NOT EXISTS ha_instances (
        id         TEXT PRIMARY KEY,
        alias      TEXT NOT NULL,
        host       TEXT NOT NULL,
        token      TEXT NOT NULL,
        enabled    INTEGER NOT NULL DEFAULT 1,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS notification_devices (
        id             TEXT PRIMARY KEY,
        ha_instance_id TEXT NOT NULL REFERENCES ha_instances(id) ON DELETE CASCADE,
        alias          TEXT NOT NULL,
        service_target TEXT NOT NULL,
        enabled        INTEGER NOT NULL DEFAULT 1,
        created_at     REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS notification_settings (
        id            INTEGER PRIMARY KEY DEFAULT 1,
        enabled       INTEGER NOT NULL DEFAULT 0,
        triggers      TEXT NOT NULL DEFAULT '[]',
        temp_threshold REAL NOT NULL DEFAULT 40.0,
        created_at    REAL NOT NULL DEFAULT 0,
        updated_at    REAL NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO notification_settings (id, created_at, updated_at)
        VALUES (1, strftime('%s','now'), strftime('%s','now'));
    """,
    # v5 — notification log: records each push notification sent
    """
    CREATE TABLE IF NOT EXISTS notification_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        ts         REAL NOT NULL,
        context    TEXT NOT NULL DEFAULT '{}',
        devices    TEXT NOT NULL DEFAULT '[]',
        ok         INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_notif_log_event ON notification_log (event_type, ts DESC);
    """,
]


async def init_db(db_path: pathlib.Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
    )
    row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
    current = row[0] or 0

    for i, sql in enumerate(_MIGRATIONS, start=1):
        if i > current:
            log.info("Applying DB migration v%d", i)
            await db.executescript(sql)
            await db.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (i,)
            )
    await db.commit()
    return db


async def get_config(db: aiosqlite.Connection, key: str, default: str = "") -> str:
    row = await (await db.execute(
        "SELECT value FROM app_config WHERE key=?", (key,)
    )).fetchone()
    return row[0] if row else default


async def set_config(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO app_config (key, value) VALUES (?,?)", (key, value)
    )
    await db.commit()


# ── HA Instances ──────────────────────────────────────────────────────────────

async def get_ha_instances(db: aiosqlite.Connection) -> list[dict]:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT id,alias,host,enabled,is_default,created_at FROM ha_instances ORDER BY created_at"
    )).fetchall()
    return [dict(r) for r in rows]


async def get_ha_instance(db: aiosqlite.Connection, instance_id: str) -> dict | None:
    db.row_factory = aiosqlite.Row
    row = await (await db.execute(
        "SELECT * FROM ha_instances WHERE id=?", (instance_id,)
    )).fetchone()
    return dict(row) if row else None


async def upsert_ha_instance(db: aiosqlite.Connection, inst: dict) -> None:
    import time
    now = time.time()
    await db.execute(
        """INSERT INTO ha_instances (id,alias,host,token,enabled,is_default,created_at)
           VALUES (:id,:alias,:host,:token,:enabled,:is_default,:now)
           ON CONFLICT(id) DO UPDATE SET
             alias=excluded.alias, host=excluded.host,
             token=CASE WHEN excluded.token='••••' THEN token ELSE excluded.token END,
             enabled=excluded.enabled, is_default=excluded.is_default""",
        {**inst, "now": now},
    )
    if inst.get("is_default"):
        await db.execute(
            "UPDATE ha_instances SET is_default=0 WHERE id!=?", (inst["id"],)
        )
    await db.commit()


async def delete_ha_instance(db: aiosqlite.Connection, instance_id: str) -> None:
    await db.execute("DELETE FROM ha_instances WHERE id=?", (instance_id,))
    await db.commit()


# ── Notification Devices ──────────────────────────────────────────────────────

async def get_notification_devices(db: aiosqlite.Connection) -> list[dict]:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        """SELECT d.id, d.ha_instance_id, d.alias, d.service_target, d.enabled, d.created_at,
                  i.alias AS instance_alias, i.host AS instance_host, i.token AS instance_token,
                  i.enabled AS instance_enabled
           FROM notification_devices d
           JOIN ha_instances i ON i.id=d.ha_instance_id
           ORDER BY d.created_at"""
    )).fetchall()
    return [dict(r) for r in rows]


async def upsert_notification_device(db: aiosqlite.Connection, dev: dict) -> None:
    import time
    now = time.time()
    await db.execute(
        """INSERT INTO notification_devices (id,ha_instance_id,alias,service_target,enabled,created_at)
           VALUES (:id,:ha_instance_id,:alias,:service_target,:enabled,:now)
           ON CONFLICT(id) DO UPDATE SET
             ha_instance_id=excluded.ha_instance_id, alias=excluded.alias,
             service_target=excluded.service_target, enabled=excluded.enabled""",
        {**dev, "now": now},
    )
    await db.commit()


async def delete_notification_device(db: aiosqlite.Connection, device_id: str) -> None:
    await db.execute("DELETE FROM notification_devices WHERE id=?", (device_id,))
    await db.commit()


# ── Notification Settings (singleton row id=1) ────────────────────────────────

async def get_notification_settings(db: aiosqlite.Connection) -> dict:
    import json
    db.row_factory = aiosqlite.Row
    row = await (await db.execute(
        "SELECT * FROM notification_settings WHERE id=1"
    )).fetchone()
    if not row:
        return {"id": 1, "enabled": 0, "triggers": [], "temp_threshold": 40.0}
    d = dict(row)
    d["triggers"] = json.loads(d["triggers"])
    return d


async def update_notification_settings(db: aiosqlite.Connection, settings: dict) -> None:
    import json, time
    triggers_json = json.dumps(settings.get("triggers", []))
    await db.execute(
        """INSERT INTO notification_settings (id, enabled, triggers, temp_threshold, created_at, updated_at)
           VALUES (1, :enabled, :triggers, :temp_threshold, :now, :now)
           ON CONFLICT(id) DO UPDATE SET
             enabled=excluded.enabled, triggers=excluded.triggers,
             temp_threshold=excluded.temp_threshold, updated_at=excluded.updated_at""",
        {
            "enabled": int(bool(settings.get("enabled", False))),
            "triggers": triggers_json,
            "temp_threshold": float(settings.get("temp_threshold", 40.0)),
            "now": time.time(),
        },
    )
    await db.commit()


# ── Notification Log ──────────────────────────────────────────────────────────

async def get_notification_log(
    db,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    import json
    db.row_factory = aiosqlite.Row
    if event_type:
        rows = await (await db.execute(
            "SELECT * FROM notification_log WHERE event_type=? ORDER BY ts DESC LIMIT ?",
            (event_type, limit),
        )).fetchall()
    else:
        rows = await (await db.execute(
            "SELECT * FROM notification_log ORDER BY ts DESC LIMIT ?",
            (limit,),
        )).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["context"] = json.loads(d["context"])
        d["devices"] = json.loads(d["devices"])
        result.append(d)
    return result


async def get_notification_log_stats(db) -> dict:
    """Return {event_type: {count, last_ts}} for all event types."""
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT event_type, COUNT(*) AS cnt, MAX(ts) AS last_ts FROM notification_log GROUP BY event_type"
    )).fetchall()
    return {r["event_type"]: {"count": r["cnt"], "last_ts": r["last_ts"]} for r in rows}
