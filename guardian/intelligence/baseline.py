from __future__ import annotations

import collections
import time
from typing import Any, Dict, List, Optional

import numpy as np

from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..storage.sqlite_store import SQLiteStore
from ..utils.logger import get_logger

_log = get_logger(__name__)

STATIC_METRICS = {
    'cpu.count_logical', 'cpu.count_physical', 'cpu.freq_max_mhz',
    'memory.total_bytes', 'memory.hugepages_total', 'memory.hugepages_size_kb',
    'memory.fd_max',
}

_FLUSH_INTERVAL = 300  # 5 minutes


def _flatten(metrics: Dict[str, Any], prefix: str = "") -> Dict[str, float]:
    result: Dict[str, float] = {}
    for k, v in metrics.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, full_key))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            result[full_key] = float(v)
    return result


class BaselineEngine:
    """
    Rolling window of recent metric values, per (collector, metric_key).
    Uses collections.deque(maxlen=samples_per_window) for O(1) append + auto-eviction.
    """

    def __init__(self, config: GuardianConfig, store: SQLiteStore) -> None:
        self.config = config
        self._store = store
        interval = config.collector.interval_seconds
        self._interval_seconds = interval
        self._samples_per_window = max(
            1, int(config.intelligence.baseline_window_hours * 3600 / interval)
        )
        self._windows: Dict[str, collections.deque] = {}
        self._start_time = time.time()
        self._last_flush = 0.0
        self.load_from_store()

    def update(self, snapshot: MetricSnapshot) -> None:
        collector = snapshot.collector_name
        if collector not in self.config.intelligence.anomaly_collectors:
            return
        if not snapshot.metrics or snapshot.status == "error":
            return
        flat = _flatten(snapshot.metrics)
        for metric_key, value in flat.items():
            full_key = f"{collector}.{metric_key}"
            if full_key in STATIC_METRICS:
                continue
            window_key = f"{collector}:{metric_key}"
            if window_key not in self._windows:
                self._windows[window_key] = collections.deque(maxlen=self._samples_per_window)
            self._windows[window_key].append(value)

    def update_all(self, snapshots: Dict[str, MetricSnapshot]) -> None:
        for snap in snapshots.values():
            self.update(snap)

    def get_stats(self, collector: str, metric_key: str) -> Optional[Dict]:
        window_key = f"{collector}:{metric_key}"
        window = self._windows.get(window_key)
        min_samples = self.config.intelligence.baseline_min_samples
        if window is None or len(window) < min_samples:
            return None
        arr = np.array(list(window))
        return {
            "mean": float(np.mean(arr)),
            "stddev": float(np.std(arr, ddof=1)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "sample_count": len(window),
            "is_ready": True,
        }

    def get_recent_values(self, collector: str, metric_key: str, n: int = 60) -> Optional[List[float]]:
        window_key = f"{collector}:{metric_key}"
        window = self._windows.get(window_key)
        if window is None or len(window) == 0:
            return None
        values = list(window)
        return values[-n:] if len(values) > n else values

    def is_warming_up(self) -> bool:
        return (time.time() - self._start_time) < (
            self.config.intelligence.warmup_minutes * 60
        )

    def flush_to_store(self) -> None:
        now = time.time()
        if now - self._last_flush < _FLUSH_INTERVAL:
            return
        min_samples = self.config.intelligence.baseline_min_samples
        window_start = now - self._samples_per_window * self._interval_seconds
        for window_key, window in self._windows.items():
            if len(window) < min_samples:
                continue
            try:
                collector, metric_key = window_key.split(":", 1)
                arr = np.array(list(window))
                stats = {
                    "mean": float(np.mean(arr)),
                    "stddev": float(np.std(arr, ddof=1)),
                    "p95": float(np.percentile(arr, 95)),
                    "p99": float(np.percentile(arr, 99)),
                    "sample_count": len(window),
                }
                self._store.insert_baseline(
                    collector, metric_key, window_start, now, stats
                )
            except Exception as exc:
                _log.debug("flush_to_store error for %s: %s", window_key, exc)
        self._last_flush = now

    def load_from_store(self) -> None:
        try:
            conn = self._store._conn()
            rows = conn.execute(
                "SELECT DISTINCT collector_name, metric_key FROM baselines"
            ).fetchall()
            min_samples = self.config.intelligence.baseline_min_samples
            for row in rows:
                collector = row["collector_name"]
                metric_key = row["metric_key"]
                stored = self._store.get_latest_baseline(collector, metric_key)
                if not stored:
                    continue
                window_key = f"{collector}:{metric_key}"
                if window_key not in self._windows:
                    self._windows[window_key] = collections.deque(
                        maxlen=self._samples_per_window
                    )
                # Pre-seed with stored mean to avoid cold-start suppression
                n = min(int(stored["sample_count"]), min_samples)
                mean = stored["mean"]
                for _ in range(n):
                    self._windows[window_key].append(mean)
        except Exception as exc:
            _log.debug("load_from_store error: %s", exc)
