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


class SQLiteStore:
    def __init__(self, config: StorageConfig) -> None:
        self.db_path = config.db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(_local, "conn") or _local.conn is None or getattr(_local, "conn_path", None) != self.db_path:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            _local.conn = conn
            _local.conn_path = self.db_path
        return _local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metric_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_name TEXT NOT NULL,
                timestamp REAL NOT NULL,
                metrics_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok'
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON metric_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snapshots_collector ON metric_snapshots(collector_name, timestamp);

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                sent_to TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_alerts_fp ON alerts(fingerprint, timestamp);
        """)
        conn.commit()

    def insert_snapshot(self, snapshot: MetricSnapshot) -> None:
        try:
            conn = self._conn()
            conn.execute(
                "INSERT INTO metric_snapshots (collector_name, timestamp, metrics_json, status) VALUES (?, ?, ?, ?)",
                (snapshot.collector_name, snapshot.timestamp, json.dumps(snapshot.metrics), snapshot.status),
            )
            conn.commit()
        except Exception as exc:
            _log.error("insert_snapshot error: %s", exc)

    def insert_alert(self, alert: Alert, sent_to: List[str]) -> None:
        try:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO alerts (id, fingerprint, severity, category, title, message, metrics_json, timestamp, sent_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert.id, alert.fingerprint, alert.severity.name, alert.category,
                    alert.title, alert.message, json.dumps(alert.metrics),
                    alert.timestamp, ",".join(sent_to),
                ),
            )
            conn.commit()
        except Exception as exc:
            _log.error("insert_alert error: %s", exc)

    def query_snapshots(self, collector: str, since: float, until: float, limit: int = 1000) -> List[Dict]:
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM metric_snapshots WHERE collector_name=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp DESC LIMIT ?",
                (collector, since, until, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.error("query_snapshots error: %s", exc)
            return []

    def query_alerts(self, since: float, until: float, severity: Optional[str] = None, limit: int = 500) -> List[Dict]:
        try:
            conn = self._conn()
            if severity:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE timestamp>=? AND timestamp<=? AND severity=? ORDER BY timestamp DESC LIMIT ?",
                    (since, until, severity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp DESC LIMIT ?",
                    (since, until, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.error("query_alerts error: %s", exc)
            return []

    def latest_snapshot(self, collector: str) -> Optional[Dict]:
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM metric_snapshots WHERE collector_name=? ORDER BY timestamp DESC LIMIT 1",
                (collector,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            _log.error("latest_snapshot error: %s", exc)
            return None

    def prune_old_data(self, retention_days: int) -> int:
        try:
            cutoff = time.time() - retention_days * 86400
            conn = self._conn()
            cur = conn.execute("DELETE FROM metric_snapshots WHERE timestamp < ?", (cutoff,))
            cur2 = conn.execute("DELETE FROM alerts WHERE timestamp < ?", (cutoff,))
            conn.commit()
            return (cur.rowcount or 0) + (cur2.rowcount or 0)
        except Exception as exc:
            _log.error("prune_old_data error: %s", exc)
            return 0
