from __future__ import annotations

import time

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.alerter.router import AlertRouter
from guardian.collector.base import MetricSnapshot
from guardian.config.schema import GuardianConfig, ThresholdConfig

from .conftest import make_config, make_snapshot


def _router(alerters=None, **threshold_kwargs):
    cfg = make_config()
    for k, v in threshold_kwargs.items():
        setattr(cfg.thresholds, k, v)
    return AlertRouter(cfg, alerters or [])


def test_cpu_critical_threshold_fires_alert():
    router = _router(cpu_critical=95.0, cpu_warn=80.0)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 96.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    alerts = router.evaluate(snaps)
    critical = [a for a in alerts if a.title == "Critical CPU Usage"]
    assert len(critical) >= 1
    assert critical[0].severity == AlertSeverity.CRITICAL


def test_cpu_warn_threshold():
    router = _router(cpu_critical=95.0, cpu_warn=80.0)
    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 85.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    alerts = router.evaluate(snaps)
    warns = [a for a in alerts if a.title == "High CPU Usage"]
    assert warns[0].severity == AlertSeverity.WARN


def test_oom_kill_is_emergency():
    router = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 10.0, "swap_sout_per_sec": 0.0, "oom_kill_count_new": 1
    })}
    alerts = router.evaluate(snaps)
    emergencies = [a for a in alerts if a.severity == AlertSeverity.EMERGENCY]
    assert len(emergencies) >= 1


def test_spot_interruption_is_emergency():
    router = _router()
    snaps = {"ec2": make_snapshot("ec2", {
        "is_ec2": True,
        "instance_id": "i-test",
        "spot_interruption": {"scheduled": True, "action": "terminate", "notice_time": "2024-01-01T00:00:00Z"},
    })}
    alerts = router.evaluate(snaps)
    emergencies = [a for a in alerts if a.severity == AlertSeverity.EMERGENCY]
    assert any("SPOT" in a.title for a in emergencies)


def test_alert_dedup_within_cooldown():
    class FakeAlerter:
        name = "fake"
        sent = []
        def is_enabled(self): return True
        def send(self, alert): self.sent.append(alert); return True

    alerter = FakeAlerter()
    router = _router(alerters=[alerter], cpu_critical=95.0, cpu_warn=80.0)
    router.config.alerts.cooldown_seconds = 300

    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 96.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    alerts1 = router.evaluate(snaps)
    router.dispatch(alerts1)
    first_count = len(alerter.sent)

    alerts2 = router.evaluate(snaps)
    router.dispatch(alerts2)
    # Same fingerprint within cooldown — should not send again
    assert len(alerter.sent) == first_count


def test_alert_escalation_after_timeout():
    class FakeAlerter:
        name = "fake"
        sent = []
        def is_enabled(self): return True
        def send(self, alert): self.sent.append(alert); return True

    alerter = FakeAlerter()
    router = _router(alerters=[alerter], cpu_warn=80.0, cpu_critical=95.0)
    router.config.alerts.cooldown_seconds = 300
    router.config.alerts.escalation_minutes = 15

    snaps = {"cpu": make_snapshot("cpu", {"percent_total": 85.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    alerts = router.evaluate(snaps)
    router.dispatch(alerts)

    # Simulate 16 minutes passing by backdating first_seen
    from guardian.alerter.router import _fingerprint
    fp = _fingerprint("cpu", "High CPU Usage")
    if fp in router._active:
        orig, first_seen, last_sent = router._active[fp]
        router._active[fp] = (orig, time.time() - 16 * 60, last_sent)

    alerts2 = router.evaluate(snaps)
    router.dispatch(alerts2)

    escalated = [a for a in alerter.sent if a.severity == AlertSeverity.CRITICAL]
    assert len(escalated) >= 1


def test_recovery_alert_fired():
    class FakeAlerter:
        name = "fake"
        sent = []
        def is_enabled(self): return True
        def send(self, alert): self.sent.append(alert); return True

    alerter = FakeAlerter()
    router = _router(alerters=[alerter], cpu_warn=80.0, cpu_critical=95.0)
    router.config.alerts.recovery_notifications = True
    router.config.alerts.cooldown_seconds = 0

    high_snaps = {"cpu": make_snapshot("cpu", {"percent_total": 85.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    router.dispatch(router.evaluate(high_snaps))

    low_snaps = {"cpu": make_snapshot("cpu", {"percent_total": 10.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    recovery_alerts = router._check_recovery(low_snaps)
    assert any("Recovered" in a.title for a in recovery_alerts)
