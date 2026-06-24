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
