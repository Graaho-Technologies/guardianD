from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from ..alerter.base import Alert
from ..collector.base import MetricSnapshot
from ..config.schema import StorageConfig
from ..utils.logger import get_logger

_log = get_logger(__name__)
_local = threading.local()
_write_lock = threading.Lock()

_PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA cache_size   = -32000;
PRAGMA temp_store   = MEMORY;
PRAGMA mmap_size    = 134217728;
"""


class SQLiteStore:
    def __init__(self, config: StorageConfig) -> None:
        self.db_path = config.db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if (
            not hasattr(_local, "conn")
            or _local.conn is None
            or getattr(_local, "conn_path", None) != self.db_path
        ):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.executescript(_PRAGMAS)
            conn.row_factory = sqlite3.Row
            _local.conn = conn
            _local.conn_path = self.db_path
        return _local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metric_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_name TEXT    NOT NULL,
                timestamp      REAL    NOT NULL,
                metrics_json   TEXT    NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'ok',
                duration_ms    REAL    NOT NULL DEFAULT 0.0
            );
            CREATE INDEX IF NOT EXISTS idx_snap_ts        ON metric_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snap_collector ON metric_snapshots(collector_name, timestamp);

            CREATE TABLE IF NOT EXISTS alerts (
                id           TEXT  PRIMARY KEY,
                fingerprint  TEXT  NOT NULL,
                severity     TEXT  NOT NULL,
                category     TEXT  NOT NULL,
                title        TEXT  NOT NULL,
                message      TEXT  NOT NULL,
                metrics_json TEXT  NOT NULL,
                timestamp    REAL  NOT NULL,
                is_recovery  INTEGER NOT NULL DEFAULT 0,
                sent_to      TEXT  NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_alert_ts  ON alerts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_alert_fp  ON alerts(fingerprint, timestamp);
            CREATE INDEX IF NOT EXISTS idx_alert_sev ON alerts(severity, timestamp);

            CREATE TABLE IF NOT EXISTS baselines (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_name TEXT    NOT NULL,
                metric_key     TEXT    NOT NULL,
                window_start   REAL    NOT NULL,
                window_end     REAL    NOT NULL,
                mean           REAL    NOT NULL,
                stddev         REAL    NOT NULL,
                sample_count   INTEGER NOT NULL,
                p95            REAL    NOT NULL,
                p99            REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_baseline_key ON baselines(collector_name, metric_key, window_end);
        """)
        conn.commit()

    def insert_snapshot(self, snapshot: MetricSnapshot) -> None:
        try:
            with _write_lock:
                conn = self._conn()
                conn.execute(
                    "INSERT INTO metric_snapshots "
                    "(collector_name, timestamp, metrics_json, status, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        snapshot.collector_name,
                        snapshot.timestamp,
                        json.dumps(snapshot.metrics),
                        snapshot.status,
                        snapshot.collection_duration_ms,
                    ),
                )
                conn.commit()
        except Exception as exc:
            _log.error("insert_snapshot error: %s", exc)

    def insert_alert(self, alert: Alert, sent_to: List[str]) -> None:
        try:
            with _write_lock:
                conn = self._conn()
                conn.execute(
                    "INSERT OR REPLACE INTO alerts "
                    "(id, fingerprint, severity, category, title, message, "
                    "metrics_json, timestamp, is_recovery, sent_to) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        alert.id,
                        alert.fingerprint,
                        alert.severity.name,
                        alert.category,
                        alert.title,
                        alert.message,
                        json.dumps(alert.metrics),
                        alert.timestamp,
                        1 if alert.is_recovery else 0,
                        ",".join(sent_to),
                    ),
                )
                conn.commit()
        except Exception as exc:
            _log.error("insert_alert error: %s", exc)

    def query_snapshots(
        self,
        collector: str,
        since: float,
        until: Optional[float] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        try:
            if until is None:
                until = time.time()
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM metric_snapshots "
                "WHERE collector_name=? AND timestamp>=? AND timestamp<=? "
                "ORDER BY timestamp DESC LIMIT ?",
                (collector, since, until, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.error("query_snapshots error: %s", exc)
            return []

    def latest_snapshot(self, collector: str) -> Optional[Dict]:
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM metric_snapshots "
                "WHERE collector_name=? ORDER BY timestamp DESC LIMIT 1",
                (collector,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            _log.error("latest_snapshot error: %s", exc)
            return None

    def query_alerts(
        self,
        since: float,
        until: Optional[float] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        try:
            if until is None:
                until = time.time()
            conn = self._conn()
            if severity:
                rows = conn.execute(
                    "SELECT * FROM alerts "
                    "WHERE timestamp>=? AND timestamp<=? AND severity=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (since, until, severity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts "
                    "WHERE timestamp>=? AND timestamp<=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (since, until, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.error("query_alerts error: %s", exc)
            return []

    def get_active_alerts(self, active_fingerprints: List[str]) -> List[Dict]:
        if not active_fingerprints:
            return []
        try:
            conn = self._conn()
            placeholders = ",".join("?" * len(active_fingerprints))
            rows = conn.execute(
                f"SELECT * FROM alerts WHERE fingerprint IN ({placeholders}) "
                "ORDER BY timestamp DESC",
                active_fingerprints,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.error("get_active_alerts error: %s", exc)
            return []

    def insert_baseline(
        self,
        collector: str,
        metric_key: str,
        window_start: float,
        window_end: float,
        stats: Dict,
    ) -> None:
        try:
            with _write_lock:
                conn = self._conn()
                conn.execute(
                    "INSERT INTO baselines "
                    "(collector_name, metric_key, window_start, window_end, "
                    "mean, stddev, sample_count, p95, p99) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        collector,
                        metric_key,
                        window_start,
                        window_end,
                        stats.get("mean", 0.0),
                        stats.get("stddev", 0.0),
                        stats.get("sample_count", 0),
                        stats.get("p95", 0.0),
                        stats.get("p99", 0.0),
                    ),
                )
                conn.commit()
        except Exception as exc:
            _log.error("insert_baseline error: %s", exc)

    def get_latest_baseline(self, collector: str, metric_key: str) -> Optional[Dict]:
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM baselines "
                "WHERE collector_name=? AND metric_key=? "
                "ORDER BY window_end DESC LIMIT 1",
                (collector, metric_key),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            _log.error("get_latest_baseline error: %s", exc)
            return None

    def prune_old_data(
        self,
        metric_days: Optional[int] = None,
        alert_days: Optional[int] = None,
        baseline_days: Optional[int] = None,
        *,
        retention_days: Optional[int] = None,
    ) -> int:
        """Delete old rows. Accepts retention_days shorthand or per-table day counts."""
        if retention_days is not None:
            metric_days = alert_days = baseline_days = retention_days
        metric_days = metric_days or 7
        alert_days = alert_days or 30
        baseline_days = baseline_days or 30
        try:
            now = time.time()
            with _write_lock:
                conn = self._conn()
                r1 = conn.execute(
                    "DELETE FROM metric_snapshots WHERE timestamp < ?",
                    (now - metric_days * 86400,),
                )
                r2 = conn.execute(
                    "DELETE FROM alerts WHERE timestamp < ?",
                    (now - alert_days * 86400,),
                )
                r3 = conn.execute(
                    "DELETE FROM baselines WHERE window_end < ?",
                    (now - baseline_days * 86400,),
                )
                conn.commit()
            return (r1.rowcount or 0) + (r2.rowcount or 0) + (r3.rowcount or 0)
        except Exception as exc:
            _log.error("prune_old_data error: %s", exc)
            return 0
