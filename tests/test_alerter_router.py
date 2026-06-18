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
    router.config.alerts.breach_cycles_to_alert = 1  # isolate cooldown from debounce

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
    router.config.alerts.breach_cycles_to_alert = 1  # isolate escalation from debounce

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
    router.config.alerts.breach_cycles_to_alert = 1  # isolate recovery from debounce
    router.config.alerts.recovery_clear_cycles = 1   # recover on first sustained dip

    high_snaps = {"cpu": make_snapshot("cpu", {"percent_total": 85.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    router.dispatch(router.evaluate(high_snaps))

    low_snaps = {"cpu": make_snapshot("cpu", {"percent_total": 10.0, "times_steal": 0.0, "times_iowait": 0.0, "load_avg_normalized_1m": 0.0})}
    recovery_alerts = router._check_recovery(low_snaps)
    assert any("Recovered" in a.title for a in recovery_alerts)


class _Capture:
    name = "fake"

    def __init__(self):
        self.sent = []

    def is_enabled(self):
        return True

    def send(self, alert):
        self.sent.append(alert)
        return True


def _cpu_snaps(pct):
    return {"cpu": make_snapshot("cpu", {
        "percent_total": pct, "times_steal": 0.0, "times_iowait": 0.0,
        "load_avg_normalized_1m": 0.0,
    })}


# --- FIX-1: breach debounce -------------------------------------------------

def test_breach_debounce_holds_first_cycle():
    alerter = _Capture()
    router = _router(alerters=[alerter], cpu_warn=80.0, cpu_critical=95.0)
    router.config.alerts.breach_cycles_to_alert = 2

    # First breach cycle: held, nothing sent yet.
    router.dispatch(router.evaluate(_cpu_snaps(96.0)))
    assert len(alerter.sent) == 0
    # Second consecutive breach cycle: now fires.
    router.dispatch(router.evaluate(_cpu_snaps(96.0)))
    assert len(alerter.sent) == 1


def test_breach_debounce_resets_on_clear():
    alerter = _Capture()
    router = _router(alerters=[alerter], cpu_warn=80.0, cpu_critical=95.0)
    router.config.alerts.breach_cycles_to_alert = 2

    # Flap: breach, clear, breach — never two *consecutive* breaches → no alert.
    router.dispatch(router.evaluate(_cpu_snaps(96.0)))   # streak 1
    router.dispatch(router.evaluate(_cpu_snaps(10.0)))   # clear → streak reset
    router.dispatch(router.evaluate(_cpu_snaps(96.0)))   # streak 1 again
    assert len(alerter.sent) == 0


def test_emergency_bypasses_debounce():
    alerter = _Capture()
    router = _router(alerters=[alerter])
    router.config.alerts.breach_cycles_to_alert = 5

    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 10.0, "swap_sout_per_sec": 0.0,
        "oom_kill_count_new": 1,
    })}
    # OOM is EMERGENCY — must fire on the very first cycle despite debounce=5.
    router.dispatch(router.evaluate(snaps))
    assert any(a.severity == AlertSeverity.EMERGENCY for a in alerter.sent)


# --- FIX-1: recovery hysteresis --------------------------------------------

def test_recovery_hysteresis_requires_sustained_clear():
    alerter = _Capture()
    router = _router(alerters=[alerter], cpu_warn=80.0, cpu_critical=95.0)
    router.config.alerts.cooldown_seconds = 0
    router.config.alerts.breach_cycles_to_alert = 1
    router.config.alerts.recovery_clear_cycles = 2

    router.dispatch(router.evaluate(_cpu_snaps(96.0)))  # active

    # First dip: hysteresis holds, no recovery yet.
    assert router._check_recovery(_cpu_snaps(10.0)) == []
    # Re-breach before clearing: streak abandoned, still no recovery.
    assert router._check_recovery(_cpu_snaps(96.0)) == []
    # Two consecutive dips: now recovers.
    assert router._check_recovery(_cpu_snaps(10.0)) == []
    recoveries = router._check_recovery(_cpu_snaps(10.0))
    assert any("Recovered" in a.title for a in recoveries)


# --- FIX-3: app-health consecutive-failure gate -----------------------------

def _app_health_snap(consecutive_failures):
    return {"app_health": make_snapshot("app_health", {"checks": [{
        "name": "web", "type": "http", "healthy": False,
        "error": "timeout", "consecutive_failures": consecutive_failures,
    }]})}


def test_app_health_single_failure_suppressed():
    from guardian.config.schema import AppHealthCheck
    router = _router()
    router.config.app_health_checks = [AppHealthCheck(name="web", failure_threshold=2)]
    alerts = router.evaluate(_app_health_snap(1))
    assert not [a for a in alerts if a.category == "app_health"]


def test_app_health_sustained_failure_alerts():
    from guardian.config.schema import AppHealthCheck
    router = _router()
    router.config.app_health_checks = [AppHealthCheck(name="web", failure_threshold=2)]
    alerts = router.evaluate(_app_health_snap(2))
    assert any(a.category == "app_health" for a in alerts)


# --- FIX-4: disk await minimum-ops floor ------------------------------------

def _disk_io_snap(await_ms, total_ops):
    return {"disk": make_snapshot("disk", {"mounts": [], "io": {
        "nvme0n1": {"await_ms": await_ms, "total_ops": total_ops, "disk_type": "ebs"},
    }})}


def test_disk_latency_suppressed_on_few_ops():
    router = _router(disk_await_min_ops=50.0)
    # 1 slow op @ 60ms — below the op floor → no latency alert.
    alerts = router.evaluate(_disk_io_snap(60.0, 1))
    assert not [a for a in alerts if "Latency" in a.title]


def test_disk_latency_alerts_on_sustained_load():
    router = _router(disk_await_min_ops=50.0, disk_await_ebs_warn_ms=20.0,
                     disk_await_ebs_critical_ms=100.0)
    # 200 ops averaging 60ms — real latency under load → alert.
    alerts = router.evaluate(_disk_io_snap(60.0, 200))
    assert any("Latency" in a.title for a in alerts)


# --- FIX-5: network rate minimum-packets floor ------------------------------

def _net_iface_snap(err_rate, drop_rate, pps):
    return {"network": make_snapshot("network", {"interfaces": {"eth0": {
        "error_rate_percent": err_rate, "drop_rate_percent": drop_rate,
        "packets_sent_per_sec": pps / 2, "packets_recv_per_sec": pps / 2,
    }}, "tcp_connections": {}})}


def test_network_error_rate_suppressed_on_low_traffic():
    router = _router(network_min_pps=100.0)
    # 1 error in 5 pkt/s = 20% but below the pps floor → no alert.
    alerts = router.evaluate(_net_iface_snap(20.0, 0.0, 5))
    assert not [a for a in alerts if a.category == "network"]


def test_network_error_rate_alerts_on_real_traffic():
    router = _router(network_min_pps=100.0, network_error_rate_warn=0.1,
                     network_error_rate_critical=1.0)
    alerts = router.evaluate(_net_iface_snap(2.0, 0.0, 1000))
    assert any(a.category == "network" for a in alerts)


# --- FIX-6: D-state threshold -----------------------------------------------

def _proc_snap(dsleep_count):
    return {"process": make_snapshot("process", {
        "zombie": 0,
        "disk_sleep_procs": [{"pid": i, "name": "x", "cmdline": ""} for i in range(dsleep_count)],
    })}


def test_dstate_below_threshold_no_alert():
    router = _router(disk_sleep_warn=5, disk_sleep_critical=20)
    alerts = router.evaluate(_proc_snap(2))  # 2 procs briefly in D — normal
    assert not [a for a in alerts if "Disk-Sleep" in a.title]


def test_dstate_above_threshold_alerts_with_stable_title():
    router = _router(disk_sleep_warn=5, disk_sleep_critical=20)
    a6 = router.evaluate(_proc_snap(6))
    a8 = router.evaluate(_proc_snap(8))
    d6 = [a for a in a6 if "Disk-Sleep" in a.title]
    d8 = [a for a in a8 if "Disk-Sleep" in a.title]
    assert d6 and d8
    # Stable fingerprint across differing counts (count lives in the message).
    assert d6[0].fingerprint == d8[0].fingerprint
