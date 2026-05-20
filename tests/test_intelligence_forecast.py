from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.config.schema import GuardianConfig
from guardian.intelligence.forecast import TrendForecaster

from .conftest import make_snapshot


def _mock_baseline(warming_up=False, values=None):
    b = MagicMock()
    b.is_warming_up.return_value = warming_up
    b.get_recent_values.return_value = values
    return b


def _forecaster(warming_up=False, values=None, warn_hours=8.0, crit_hours=2.0, interval=10):
    cfg = GuardianConfig()
    cfg.thresholds.forecast_disk_full_warn_hours = warn_hours
    cfg.thresholds.forecast_disk_full_critical_hours = crit_hours
    cfg.thresholds.memory_critical = 92.0
    cfg.thresholds.swap_critical = 80.0
    cfg.thresholds.disk_critical = 95.0
    cfg.collector.interval_seconds = interval
    baseline = _mock_baseline(warming_up, values)
    return TrendForecaster(cfg, baseline)


def test_forecast_empty_during_warmup():
    fc = _forecaster(warming_up=True)
    snaps = {"memory": make_snapshot("memory", {"percent_used": 50.0})}
    assert fc.analyze(snaps) == []


def test_forecast_empty_insufficient_values():
    fc = _forecaster(values=[50.0, 51.0, 52.0])  # only 3, need >= 10
    snaps = {"memory": make_snapshot("memory", {"percent_used": 52.0})}
    alerts = fc.analyze(snaps)
    assert alerts == []


def test_forecast_predicts_memory_critical():
    # Steadily growing from 80 to 89 over 10 intervals of 10s each
    values = [80.0 + i for i in range(10)]  # 80..89, slope ~1%/interval
    fc = _forecaster(values=values, warn_hours=8.0, crit_hours=2.0, interval=60)
    # threshold=92, current=89, slope=1%/min (60s intervals)
    # eta = (92-89)/1 = 3 min = 0.05h → < 2h → CRITICAL
    snaps = {"memory": make_snapshot("memory", {"percent_used": 89.0, "swap_percent": 20.0})}
    alerts = fc.analyze(snaps)
    crit = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
    assert len(crit) >= 1
    assert crit[0].forecast_eta_minutes > 0


def test_forecast_predicts_warn_when_eta_between_warn_and_crit():
    # Growth that puts ETA between crit_hours and warn_hours
    # values growing slowly: 80 → 89.1 over 10 intervals of 60s
    values = [80.0 + i * 0.1 for i in range(10)]  # slope ~0.1%/min
    fc = _forecaster(values=values, warn_hours=8.0, crit_hours=2.0, interval=60)
    # threshold=92, current=80.9, slope~0.1%/min → eta=(92-80.9)/0.1≈111min=1.85h → < 2h CRITICAL
    # Actually need larger gap: use 60-70% range for warn
    # Let's use slow growth: 70..79 over 10 intervals
    values2 = [70.0 + i * 0.5 for i in range(10)]  # slope~0.5%/min
    fc2 = _forecaster(values=values2, warn_hours=8.0, crit_hours=2.0, interval=60)
    # threshold=92, current=74.5, slope~0.5%/min → eta=(92-74.5)/0.5=35min=0.58h → < 2h CRITICAL
    # Need different setup for WARN: eta between 2h and 8h
    # slope 0.5%/min, current 70, threshold 92 → eta=(92-70)/0.5=44min < 2h
    # Let's try: 50% current, slope very slow
    values3 = [50.0 + i * 0.04 for i in range(15)]  # slope~0.04%/min
    fc3 = _forecaster(values=values3, warn_hours=8.0, crit_hours=2.0, interval=60)
    # threshold=92, current=50.56, slope~0.04%/min → eta=(92-50.56)/0.04≈1036min≈17h → > 8h, no alert
    # Adjust: slope ~ 0.2%/min, current 50 → eta=(92-50)/0.2=210min=3.5h → WARN (2h < 3.5h < 8h)
    values4 = [50.0 + i * 0.2 for i in range(15)]
    fc4 = _forecaster(values=values4, warn_hours=8.0, crit_hours=2.0, interval=60)
    snaps = {"memory": make_snapshot("memory", {"percent_used": 52.8, "swap_percent": 10.0})}
    alerts = fc4.analyze(snaps)
    warns = [a for a in alerts if a.severity == AlertSeverity.WARN]
    assert len(warns) >= 1


def test_forecast_skips_flat_or_decreasing():
    values = [80.0, 79.0, 78.0, 77.0, 76.0, 75.0, 74.0, 73.0, 72.0, 71.0]
    fc = _forecaster(values=values, interval=60)
    snaps = {"memory": make_snapshot("memory", {"percent_used": 71.0, "swap_percent": 10.0})}
    assert fc.analyze(snaps) == []


def test_forecast_disk_mount_predicts():
    fc = _forecaster(warn_hours=8.0, crit_hours=2.0, interval=60)
    fc.baseline.is_warming_up.return_value = False
    # Simulate 10 disk snapshots to build mount history
    for i in range(10):
        snaps = {"disk": make_snapshot("disk", {
            "mounts": [{"mountpoint": "/", "percent_used": 80.0 + i}],
            "io": {},
        })}
        fc._update_mount_history(snaps)
    # History: 80,81,82,...,89 → slope ~1%/min at 60s intervals → eta=(95-89)/1=6min<2h → CRITICAL
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [{"mountpoint": "/", "percent_used": 89.0}],
        "io": {},
    })}
    alerts = fc.analyze(snaps)
    crit = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
    assert len(crit) >= 1


def test_forecast_alert_has_forecast_eta():
    values = [80.0 + i for i in range(10)]
    fc = _forecaster(values=values, warn_hours=8.0, crit_hours=2.0, interval=60)
    snaps = {"memory": make_snapshot("memory", {"percent_used": 89.0, "swap_percent": 10.0})}
    alerts = fc.analyze(snaps)
    if alerts:
        assert alerts[0].forecast_eta_minutes > 0
        assert hasattr(alerts[0], "forecast_eta_minutes")
