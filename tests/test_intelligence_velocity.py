from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.config.schema import GuardianConfig
from guardian.intelligence.velocity import VelocityDetector

from .conftest import make_snapshot


def _mock_baseline(warming_up=False):
    b = MagicMock()
    b.is_warming_up.return_value = warming_up
    return b


def _detector(warming_up=False):
    cfg = GuardianConfig()
    cfg.thresholds.velocity_spike_warn_pct = 40.0
    cfg.thresholds.velocity_spike_critical_pct = 70.0
    cfg.thresholds.cpu_warn = 80.0
    det = VelocityDetector(cfg, _mock_baseline(warming_up))
    return det


def test_velocity_empty_first_collection():
    det = _detector()
    snaps = {"memory": make_snapshot("memory", {"percent_used": 50.0})}
    # First call: collection_count becomes 1 (< 2), no alerts
    result = det.analyze(snaps)
    assert result == []


def test_velocity_empty_during_warmup():
    det = _detector(warming_up=True)
    # Seed prev values via first call
    snaps = {"memory": make_snapshot("memory", {"percent_used": 50.0})}
    det.analyze(snaps)
    # Second call still warming up
    snaps2 = {"memory": make_snapshot("memory", {"percent_used": 90.0})}
    result = det.analyze(snaps2)
    assert result == []


def test_velocity_detects_warn_spike():
    det = _detector()
    # First call seeds prev
    det.analyze({"memory": make_snapshot("memory", {"percent_used": 50.0})})
    # Second call: 50 → 75 = +50% → WARN (>= 40%)
    alerts = det.analyze({"memory": make_snapshot("memory", {"percent_used": 75.0})})
    warn_alerts = [a for a in alerts if a.severity == AlertSeverity.WARN]
    assert len(warn_alerts) >= 1


def test_velocity_detects_critical_spike():
    det = _detector()
    det.analyze({"memory": make_snapshot("memory", {"percent_used": 50.0})})
    # 50 → 90 = +80% → CRITICAL (>= 70%)
    alerts = det.analyze({"memory": make_snapshot("memory", {"percent_used": 90.0})})
    critical = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
    assert len(critical) >= 1


def test_velocity_no_alert_below_spike_threshold():
    det = _detector()
    det.analyze({"memory": make_snapshot("memory", {"percent_used": 50.0})})
    # 50 → 60 = +20% → below warn (40%), no alert
    alerts = det.analyze({"memory": make_snapshot("memory", {"percent_used": 60.0})})
    assert alerts == []


def test_velocity_skips_cpu_when_prev_above_warn():
    det = _detector()
    # Seed CPU prev at 85 (above cpu_warn=80)
    det.analyze({"cpu": make_snapshot("cpu", {"percent_total": 85.0})})
    # 85 → 95 = +12% — skipped because prev was already above warn
    alerts = det.analyze({"cpu": make_snapshot("cpu", {"percent_total": 95.0})})
    cpu_alerts = [a for a in alerts if "CPU" in a.title]
    assert len(cpu_alerts) == 0


def test_velocity_no_alert_on_decrease():
    det = _detector()
    det.analyze({"memory": make_snapshot("memory", {"percent_used": 80.0})})
    # 80 → 50 = -37.5% (decrease), no alert
    alerts = det.analyze({"memory": make_snapshot("memory", {"percent_used": 50.0})})
    assert alerts == []


def test_velocity_skips_when_prev_below_one():
    det = _detector()
    # Prev = 0.5 (< 1.0) — should skip
    det._prev_values['memory.percent_used'] = 0.5
    det._collection_count = 2  # past first-collection guard
    snaps = {"memory": make_snapshot("memory", {"percent_used": 50.0})}
    alerts = det.analyze(snaps)
    assert alerts == []


def test_velocity_suppresses_low_baseline_iops_spike():
    # Regression: idle-box disk IOPS swinging 1.2 -> 16 is +1233% but only
    # +14.8 IOPS — below the 500-IOPS absolute floor, so it must NOT alert.
    det = _detector()
    det.analyze({"disk": make_snapshot("disk", {"total_iops": 1.2})})
    alerts = det.analyze({"disk": make_snapshot("disk", {"total_iops": 16.0})})
    assert alerts == []


def test_velocity_fires_on_large_absolute_iops_spike():
    # A genuine spike: 1000 -> 2000 IOPS is +100% AND +1000 IOPS (>= 500 floor).
    det = _detector()
    det.analyze({"disk": make_snapshot("disk", {"total_iops": 1000.0})})
    alerts = det.analyze({"disk": make_snapshot("disk", {"total_iops": 2000.0})})
    assert any(a.severity == AlertSeverity.CRITICAL for a in alerts)


def test_velocity_suppresses_tiny_tcp_connection_jump():
    # 1 -> 3 established connections is +200% but only +2 (< 100 floor): noise.
    det = _detector()
    det.analyze({"network": make_snapshot(
        "network", {"tcp_connections": {"established": 1.0}})})
    alerts = det.analyze({"network": make_snapshot(
        "network", {"tcp_connections": {"established": 3.0}})})
    assert alerts == []
