from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.config.schema import GuardianConfig
from guardian.intelligence.anomaly import AnomalyDetector

from .conftest import make_snapshot


def _mock_baseline(is_warming_up=False, stats=None):
    b = MagicMock()
    b.is_warming_up.return_value = is_warming_up
    b.get_stats.return_value = stats
    return b


def _detector(warming_up=False, stats=None):
    cfg = GuardianConfig()
    cfg.thresholds.anomaly_zscore_warn = 2.0
    cfg.thresholds.anomaly_zscore_critical = 3.0
    baseline = _mock_baseline(is_warming_up=warming_up, stats=stats)
    return AnomalyDetector(cfg, baseline)


def test_anomaly_returns_empty_during_warmup():
    det = _detector(warming_up=True)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 99.0})}
    assert det.analyze(snaps) == []


def test_anomaly_detects_warn_zscore():
    stats = {"mean": 30.0, "stddev": 10.0, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    # value = 55.0 → z = (55 - 30) / 10 = 2.5 → WARN (>= 2.0 but < 3.0)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 55.0})}
    alerts = det.analyze(snaps)
    assert len(alerts) >= 1
    assert alerts[0].severity == AlertSeverity.WARN
    assert alerts[0].category == "intelligence"
    assert alerts[0].anomaly_score == pytest.approx(2.5, abs=0.01)


def test_anomaly_detects_critical_zscore():
    stats = {"mean": 30.0, "stddev": 10.0, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    # value = 65.0 → z = (65 - 30) / 10 = 3.5 → CRITICAL (>= 3.0)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 65.0})}
    alerts = det.analyze(snaps)
    critical = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
    assert len(critical) >= 1


def test_anomaly_skips_negative_zscore():
    stats = {"mean": 60.0, "stddev": 10.0, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    # value = 10.0 → z = (10 - 60) / 10 = -5 → negative, skip
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 10.0})}
    assert det.analyze(snaps) == []


def test_anomaly_skips_flat_metric():
    # stddev < 0.1
    stats = {"mean": 50.0, "stddev": 0.05, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 55.0})}
    assert det.analyze(snaps) == []


def test_anomaly_suppresses_above_threshold():
    stats = {"mean": 30.0, "stddev": 10.0, "sample_count": 50, "is_ready": True}
    cfg = GuardianConfig()
    cfg.thresholds.anomaly_zscore_warn = 2.0
    cfg.thresholds.anomaly_zscore_critical = 3.0
    cfg.thresholds.cpu_warn = 80.0
    baseline = _mock_baseline(stats=stats)
    det = AnomalyDetector(cfg, baseline)
    # cpu_percent = 85 >= cpu_warn (80) → suppress anomaly
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 85.0})}
    cpu_alerts = [a for a in det.analyze(snaps) if "percent_total" in a.title]
    assert len(cpu_alerts) == 0


def test_anomaly_returns_empty_when_no_stats():
    baseline = _mock_baseline(is_warming_up=False, stats=None)
    cfg = GuardianConfig()
    det = AnomalyDetector(cfg, baseline)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 99.0})}
    assert det.analyze(snaps) == []


def test_anomaly_skips_below_zscore_warn():
    stats = {"mean": 30.0, "stddev": 10.0, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    # value = 45.0 → z = 1.5 → below warn (2.0), no alert
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 45.0})}
    assert det.analyze(snaps) == []


def test_anomaly_suppresses_low_magnitude_deviation():
    # Regression: idle CPU with mean=3, stddev=1. value=9 → z=6 (would be
    # CRITICAL) but the raw deviation is only +6 points, below the 15-point
    # absolute floor for cpu.percent_total, so it must NOT alert.
    stats = {"mean": 3.0, "stddev": 1.0, "sample_count": 50, "is_ready": True}
    det = _detector(stats=stats)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 9.0})}
    assert det.analyze(snaps) == []
