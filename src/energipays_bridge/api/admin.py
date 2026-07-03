from __future__ import annotations

import asyncio
import collections
import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..store.db import get_config, set_config

router = APIRouter()
_log_buffer: collections.deque = collections.deque(maxlen=500)

# Logger names whose records are suppressed from the UI ring buffer.
# uvicorn.access floods it with one line per /api/points/latest poll (every 10s × N clients).
_SUPPRESSED_LOGGERS = {"uvicorn.access", "uvicorn.error"}

_db: aiosqlite.Connection | None = None


def set_log_db(db: aiosqlite.Connection) -> None:
    global _db
    _db = db


async def _write_log_db(entry: dict) -> None:
    try:
        await _db.execute(
            "INSERT INTO app_logs (ts, level, logger, msg) VALUES (?,?,?,?)",
            (entry["ts"], entry["level"], entry["name"], entry["msg"])
        )
        await _db.commit()
    except Exception:
        pass


class _BridgeLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name in _SUPPRESSED_LOGGERS:
            return
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        _log_buffer.append(entry)
        if _db is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_write_log_db(entry))
            except Exception:
                pass


def install_log_handler() -> None:
    handler = _BridgeLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


@router.get("/api/logs")
async def get_logs(request: Request) -> dict:
    db = request.app.state.db
    rows = await (await db.execute(
        "SELECT ts, level, logger, msg FROM app_logs ORDER BY ts DESC LIMIT 500"
    )).fetchall()
    logs = [{"ts": r[0], "level": r[1], "name": r[2], "msg": r[3]} for r in reversed(rows)]
    return {"logs": logs}


@router.get("/api/health")
async def health(request: Request) -> dict:
    poller = request.app.state.poller
    return {
        "status": "ok",
        "poller": {
            "connected": poller.connected if poller else False,
            "polls_total": poller.polls_total if poller else 0,
            "last_poll_ts": poller.last_poll_ts if poller else 0,
            "last_error": poller.last_error if poller else "",
        },
        "read_only": request.app.state.settings.read_only,
        "device_id": request.app.state.device_id,
    }


@router.get("/api/admin/db-stats")
async def db_stats(request: Request) -> dict:
    import os
    db = request.app.state.db
    tables = ["metric_samples", "metric_samples_archive", "app_logs", "app_config"]
    result = []
    for table in tables:
        try:
            row = await (await db.execute(
                f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {table}"
            )).fetchone()
            count = row[0] or 0
            span_days = round((row[2] - row[1]) / 86400, 1) if (row[1] and row[2] and row[2] > row[1]) else None
        except Exception:
            count, span_days = 0, None
        result.append({"table": table, "rows": count, "span_days": span_days})
    try:
        db_path = request.app.state.settings.data_path / "bridge.db"
        size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
    except Exception:
        size_mb = None
    return {
        "tables": result,
        "size_mb": size_mb,
        "metrics_enabled": getattr(request.app.state, "metrics_enabled", True),
    }


@router.post("/api/admin/metrics-enabled")
async def set_metrics_enabled(request: Request) -> dict:
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    db = request.app.state.db
    log = logging.getLogger(__name__)
    await set_config(db, "metrics_enabled", "1" if enabled else "0")
    request.app.state.metrics_enabled = enabled
    bus = request.app.state.bus
    if enabled:
        from ..store.metrics import MetricsRecorder
        recorder = MetricsRecorder(db)
        bus.subscribe(recorder)
        log.info("settings: metrics recording enabled")
    else:
        from ..store.metrics import MetricsRecorder
        removed = bus.unsubscribe_type(MetricsRecorder)
        log.info("settings: metrics recording disabled (bridge mode) — removed %d subscriber(s)", removed)
    return {"metrics_enabled": enabled}


@router.post("/api/admin/archive-now")
async def archive_now(request: Request) -> dict:
    log = logging.getLogger(__name__)
    if not getattr(request.app.state, "metrics_enabled", True):
        from fastapi import HTTPException
        raise HTTPException(400, "Metrics recording is disabled")
    db = request.app.state.db
    raw_age = int(await get_config(db, "raw_age_days", "7")) * 86400
    retention = int(await get_config(db, "retention_days", "30")) * 86400
    from ..store.metrics import archive_old_metrics, purge_old_archive
    await archive_old_metrics(db, raw_age)
    await purge_old_archive(db, retention)
    log.info("settings: manual archive-now completed (raw_age=%sd retention=%sd)",
             raw_age // 86400, retention // 86400)
    return {"ok": True}


@router.get("/api/admin/backups")
async def list_backups(request: Request) -> dict:
    import os, time as _time
    backup_dir = request.app.state.settings.data_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    files = []
    for f in sorted(backup_dir.glob("backup_*.db"), reverse=True)[:10]:
        stat = f.stat()
        files.append({
            "name": f.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "ts": stat.st_mtime,
        })
    return {"backups": files}


@router.post("/api/admin/backup")
async def create_backup(request: Request) -> dict:
    import shutil, time as _time
    log = logging.getLogger(__name__)
    settings = request.app.state.settings
    backup_dir = settings.data_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = _time.strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"backup_{ts}.db"
    await asyncio.to_thread(shutil.copy2, settings.db_path, dst)
    # Keep only last 10 backups
    old = sorted(backup_dir.glob("backup_*.db"))[:-10]
    for f in old:
        f.unlink(missing_ok=True)
    size_kb = round(dst.stat().st_size / 1024, 1)
    log.info("settings: backup created — %s (%s KB)", dst.name, size_kb)
    return {"name": dst.name, "size_kb": size_kb}


@router.get("/api/admin/backup/{filename}")
async def download_backup(filename: str, request: Request):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    backup_dir = request.app.state.settings.data_path / "backups"
    path = backup_dir / filename
    if not path.exists() or not path.name.startswith("backup_"):
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/admin/export/metrics")
async def export_metrics(request: Request, fmt: str = "json", hours: int = 24):
    import csv, io
    from fastapi.responses import StreamingResponse
    db = request.app.state.db
    cutoff = __import__("time").time() - hours * 3600
    rows = await (await db.execute(
        "SELECT device_id, point_id, ts, value FROM metric_samples WHERE ts >= ? "
        "UNION ALL "
        "SELECT device_id, point_id, ts, value FROM metric_samples_archive WHERE ts >= ? "
        "ORDER BY ts",
        (cutoff, cutoff)
    )).fetchall()
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["device_id", "point_id", "ts", "value"])
        w.writerows(rows)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="metrics_{hours}h.csv"'},
        )
    import json as _json
    data = [{"device_id": r[0], "point_id": r[1], "ts": r[2], "value": r[3]} for r in rows]
    return StreamingResponse(
        iter([_json.dumps(data, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="metrics_{hours}h.json"'},
    )


@router.get("/api/config/{key}")
async def get_cfg(key: str, request: Request) -> dict:
    val = await get_config(request.app.state.db, key)
    return {"key": key, "value": val}


class ConfigBody(BaseModel):
    value: Any


@router.put("/api/config/{key}")
async def put_cfg(key: str, body: ConfigBody, request: Request) -> dict:
    allowed = {"poll_interval", "raw_age_days", "retention_days", "log_level"}
    if key not in allowed:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown config key '{key}'")
    val = str(body.value)
    await set_config(request.app.state.db, key, val)
    return {"key": key, "value": val}
