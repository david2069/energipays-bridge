"""Metrics storage and retrieval for the SQLite time-series store."""
from __future__ import annotations

import logging
import time

import aiosqlite

from ..sample import Sample

log = logging.getLogger(__name__)

# Points we persist (subset of the full flat dict from poller._flatten)
_RECORDED_POINTS = {
    "waterTemperature1", "waterTemperature2", "waterTemperature3", "waterTemperatureAvg",
    "phasePower", "phasePowerA", "phasePowerB", "phasePowerC",
    "voltageA", "voltageB", "voltageC",
    "heaterStatus", "boostStatus", "stateOfCharge",
    "solarStreamA", "solarStreamB", "solarStreamC", "solarPower",
    "today.EEct", "today.IEct", "today.DE_h", "today.DE_e",
    "yesterday.EEct", "yesterday.IEct",
}


class MetricsRecorder:
    """SampleBus subscriber that writes numeric points to SQLite."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def __call__(self, sample: Sample) -> None:
        if sample.quality == "error" or not sample.points:
            return
        rows = [
            (sample.device_id, k, sample.ts, float(v))
            for k, v in sample.points.items()
            if (k in _RECORDED_POINTS or k.startswith("ext.")) and isinstance(v, (int, float))
        ]
        if rows:
            await self._db.executemany(
                "INSERT INTO metric_samples (device_id, point_id, ts, value) VALUES (?,?,?,?)",
                rows,
            )
            await self._db.commit()


async def query_metrics(
    db: aiosqlite.Connection,
    device_id: str,
    point_id: str,
    from_ts: float,
    to_ts: float,
    bucket_s: int = 300,
) -> list[tuple[float, float]]:
    """Return (ts, avg_value) tuples for the given point over the time range.

    Uses the archive table for old data, raw table for recent data.
    Buckets of ``bucket_s`` seconds are averaged.
    """
    sql = """
        SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, AVG(value)
        FROM (
            SELECT ts, value FROM metric_samples
             WHERE device_id=? AND point_id=? AND ts BETWEEN ? AND ?
            UNION ALL
            SELECT ts, value FROM metric_samples_archive
             WHERE device_id=? AND point_id=? AND ts BETWEEN ? AND ?
        )
        GROUP BY bucket
        ORDER BY bucket
    """
    rows = await (await db.execute(
        sql,
        (bucket_s, bucket_s, device_id, point_id, from_ts, to_ts,
         device_id, point_id, from_ts, to_ts),
    )).fetchall()
    return [(r[0] + bucket_s / 2, r[1]) for r in rows]


async def archive_old_metrics(db: aiosqlite.Connection, raw_age_s: float = 7 * 86400) -> None:
    """Downsample samples older than raw_age_s into 5-min buckets in the archive table."""
    cutoff = time.time() - raw_age_s
    await db.execute("""
        INSERT INTO metric_samples_archive (device_id, point_id, ts, value)
        SELECT device_id, point_id,
               CAST(ts / 300 AS INTEGER) * 300 AS bucket,
               AVG(value)
          FROM metric_samples
         WHERE ts < ?
         GROUP BY device_id, point_id, bucket
    """, (cutoff,))
    await db.execute("DELETE FROM metric_samples WHERE ts < ?", (cutoff,))
    await db.commit()


async def purge_old_archive(db: aiosqlite.Connection, retention_s: float = 30 * 86400) -> None:
    cutoff = time.time() - retention_s
    await db.execute("DELETE FROM metric_samples_archive WHERE ts < ?", (cutoff,))
    await db.commit()
