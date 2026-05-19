from __future__ import annotations

import time
from collections import deque
from unittest.mock import MagicMock

import pytest

from guardian.config.schema import GuardianConfig, IntelligenceConfig, CollectorConfig
from guardian.intelligence.baseline import BaselineEngine, STATIC_METRICS

from .conftest import make_snapshot


def _make_baseline(window_hours=1, min_samples=10, warmup_minutes=0, interval=10):
    cfg = GuardianConfig()
    cfg.intelligence.baseline_window_hours = window_hours
    cfg.intelligence.baseline_min_samples = min_samples
    cfg.intelligence.warmup_minutes = warmup_minutes
    cfg.intelligence.anomaly_collectors = ["cpu", "memory", "disk", "network"]
    cfg.collector.interval_seconds = interval

    store = MagicMock()
    store._conn.return_value.execute.return_value.fetchall.return_value = []
    store.get_latest_baseline.return_value = None

    return BaselineEngine(cfg, store)


def test_baseline_update_adds_to_window():
    engine = _make_baseline()
    snap = make_snapshot("cpu", {"percent_total": 50.0})
    engine.update(snap)
    values = engine.get_recent_values("cpu", "percent_total")
    assert values is not None
    assert 50.0 in values


def test_baseline_get_stats_returns_none_below_min_samples():
    engine = _make_baseline(min_samples=10)
    snap = make_snapshot("cpu", {"percent_total": 50.0})
    for _ in range(5):  # fewer than min_samples
        engine.update(snap)
    assert engine.get_stats("cpu", "percent_total") is None


def test_baseline_get_stats_returns_correct_stats():
    engine = _make_baseline(min_samples=5)
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    for v in values:
        engine.update(make_snapshot("cpu", {"percent_total": v}))
    stats = engine.get_stats("cpu", "percent_total")
    assert stats is not None
    assert stats["is_ready"] is True
    assert stats["sample_count"] == 5
    assert abs(stats["mean"] - 30.0) < 0.01
    assert stats["min"] == 10.0
    assert stats["max"] == 50.0
    assert "p50" in stats and "p95" in stats and "p99" in stats


def test_baseline_is_warming_up_initially():
    engine = _make_baseline(warmup_minutes=5)
    assert engine.is_warming_up() is True


def test_baseline_is_not_warming_up_when_warmup_zero():
    engine = _make_baseline(warmup_minutes=0)
    assert engine.is_warming_up() is False


def test_baseline_skips_static_metrics():
    engine = _make_baseline()
    snap = make_snapshot("cpu", {"count_logical": 4, "percent_total": 60.0})
    engine.update(snap)
    # count_logical is static — should not be tracked
    assert engine.get_recent_values("cpu", "count_logical") is None
    # percent_total should be tracked
    assert engine.get_recent_values("cpu", "percent_total") is not None


def test_baseline_skips_non_anomaly_collectors():
    cfg = GuardianConfig()
    cfg.intelligence.anomaly_collectors = ["cpu"]  # only cpu
    cfg.collector.interval_seconds = 10

    store = MagicMock()
    store._conn.return_value.execute.return_value.fetchall.return_value = []
    store.get_latest_baseline.return_value = None

    engine = BaselineEngine(cfg, store)
    # process is not in anomaly_collectors
    snap = make_snapshot("process", {"total_count": 100})
    engine.update(snap)
    assert engine.get_recent_values("process", "total_count") is None


def test_baseline_get_recent_values_limits_to_n():
    engine = _make_baseline(min_samples=3, window_hours=1)
    for i in range(20):
        engine.update(make_snapshot("cpu", {"percent_total": float(i)}))
    values = engine.get_recent_values("cpu", "percent_total", n=5)
    assert values is not None
    assert len(values) == 5
    assert values[-1] == 19.0  # most recent


def test_baseline_handles_nested_metrics():
    engine = _make_baseline()
    snap = make_snapshot("network", {
        "tcp_connections": {"established": 10, "close_wait": 2},
        "dns_latency_ms": 5.0,
    })
    engine.update(snap)
    assert engine.get_recent_values("network", "tcp_connections.established") is not None
    assert engine.get_recent_values("network", "dns_latency_ms") is not None


def test_baseline_flush_to_store_persists():
    engine = _make_baseline(min_samples=3)
    for _ in range(5):
        engine.update(make_snapshot("cpu", {"percent_total": 50.0}))
    # Force flush by backdating last_flush
    engine._last_flush = 0
    engine.flush_to_store()
    # store.insert_baseline should have been called
    assert engine._store.insert_baseline.called
