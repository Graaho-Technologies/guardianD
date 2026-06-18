from __future__ import annotations

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.alerter.router import AlertRouter
from guardian.config.schema import GuardianConfig

from .conftest import make_snapshot


def _router(config=None):
    cfg = config or GuardianConfig()
    cfg.instance_name = "test-host"
    cfg.environment = "test"
    return AlertRouter(cfg, [])


# ─── Memory ────────────────────────────────────────────────────────────────────

def test_memory_critical_alert():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 93.0, "swap_percent": 0.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 0.0,
        "fd_percent_used": 0.0, "oom_kill_count_new": 0,
    })}
    alerts = r.evaluate(snaps)
    crit = [a for a in alerts if a.category == "memory" and a.severity == AlertSeverity.CRITICAL]
    assert len(crit) >= 1


def test_memory_warn_alert():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 85.0, "swap_percent": 0.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 0.0,
        "fd_percent_used": 0.0, "oom_kill_count_new": 0,
    })}
    alerts = r.evaluate(snaps)
    warns = [a for a in alerts if a.category == "memory" and a.severity == AlertSeverity.WARN
             and "Memory Usage" in a.title]
    assert len(warns) >= 1


def test_memory_oom_is_emergency():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 0.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 0.0,
        "fd_percent_used": 0.0, "oom_kill_count_new": 2,
    })}
    alerts = r.evaluate(snaps)
    oom = [a for a in alerts if a.severity == AlertSeverity.EMERGENCY and a.category == "memory"]
    assert len(oom) >= 1


def test_memory_swap_critical():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 85.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 0.0,
        "fd_percent_used": 0.0, "oom_kill_count_new": 0,
    })}
    alerts = r.evaluate(snaps)
    swap = [a for a in alerts if "Swap" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(swap) >= 1


def test_memory_fd_exhaustion_warn():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 0.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 0.0,
        "fd_percent_used": 82.0, "oom_kill_count_new": 0,
    })}
    alerts = r.evaluate(snaps)
    fd = [a for a in alerts if "File Descriptor" in a.title and a.severity == AlertSeverity.WARN]
    assert len(fd) >= 1


def test_memory_dirty_pages_warn():
    r = _router()
    snaps = {"memory": make_snapshot("memory", {
        "percent_used": 50.0, "swap_percent": 0.0,
        "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 12.0,
        "fd_percent_used": 0.0, "oom_kill_count_new": 0,
    })}
    alerts = r.evaluate(snaps)
    dirty = [a for a in alerts if "Dirty" in a.title]
    assert len(dirty) >= 1


# ─── Disk ──────────────────────────────────────────────────────────────────────

def test_disk_space_critical_alert():
    r = _router()
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [{"mountpoint": "/", "percent_used": 96.0,
                    "inodes_percent": 0.0, "device": "/dev/sda1", "fstype": "ext4"}],
        "io": {},
    })}
    alerts = r.evaluate(snaps)
    crit = [a for a in alerts if a.category == "disk" and a.severity == AlertSeverity.CRITICAL
            and "Space" in a.title]
    assert len(crit) >= 1


def test_disk_space_warn_alert():
    r = _router()
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [{"mountpoint": "/data", "percent_used": 87.0,
                    "inodes_percent": 0.0, "device": "/dev/sdb1", "fstype": "ext4"}],
        "io": {},
    })}
    alerts = r.evaluate(snaps)
    warns = [a for a in alerts if a.category == "disk" and a.severity == AlertSeverity.WARN
             and "Space" in a.title]
    assert len(warns) >= 1


def test_disk_inode_critical():
    r = _router()
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [{"mountpoint": "/", "percent_used": 50.0,
                    "inodes_percent": 96.0, "device": "/dev/sda1", "fstype": "ext4"}],
        "io": {},
    })}
    alerts = r.evaluate(snaps)
    inode = [a for a in alerts if "Inode" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(inode) >= 1


def test_disk_latency_critical_ssd():
    r = _router()
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [],
        "io": {"sda": {"await_ms": 60.0, "disk_type": "ssd", "total_ops": 200}},
    })}
    alerts = r.evaluate(snaps)
    lat = [a for a in alerts if "Latency" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(lat) >= 1


def test_disk_latency_warn_ebs():
    r = _router()
    snaps = {"disk": make_snapshot("disk", {
        "mounts": [],
        "io": {"xvda": {"await_ms": 25.0, "disk_type": "ebs", "total_ops": 200}},
    })}
    alerts = r.evaluate(snaps)
    lat = [a for a in alerts if "Latency" in a.title and a.severity == AlertSeverity.WARN]
    assert len(lat) >= 1


# ─── Network ───────────────────────────────────────────────────────────────────

def test_network_dns_failing_critical():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {"close_wait": 0, "syn_recv": 0},
        "dns_healthy": False, "dns_latency_ms": -1.0,
    })}
    alerts = r.evaluate(snaps)
    dns = [a for a in alerts if "DNS" in a.title]
    assert len(dns) >= 1
    assert dns[0].severity == AlertSeverity.CRITICAL


def test_network_close_wait_critical():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {"close_wait": 600, "syn_recv": 0},
        "dns_healthy": True, "dns_latency_ms": 1.0,
    })}
    alerts = r.evaluate(snaps)
    cw = [a for a in alerts if "CLOSE_WAIT" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(cw) >= 1


def test_network_close_wait_warn():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {"close_wait": 150, "syn_recv": 0},
        "dns_healthy": True, "dns_latency_ms": 1.0,
    })}
    alerts = r.evaluate(snaps)
    cw = [a for a in alerts if "CLOSE_WAIT" in a.title and a.severity == AlertSeverity.WARN]
    assert len(cw) >= 1


def test_network_error_rate_warn():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {"eth0": {"error_rate_percent": 0.5, "drop_rate_percent": 0.0,
                                "packets_sent_per_sec": 500, "packets_recv_per_sec": 500}},
        "tcp_connections": {"close_wait": 0, "syn_recv": 0},
        "dns_healthy": True, "dns_latency_ms": 1.0,
    })}
    alerts = r.evaluate(snaps)
    err = [a for a in alerts if "Error" in a.title and "eth0" in a.title]
    assert len(err) >= 1


def test_network_drop_rate_critical():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {"eth0": {"error_rate_percent": 0.0, "drop_rate_percent": 2.0,
                                "packets_sent_per_sec": 500, "packets_recv_per_sec": 500}},
        "tcp_connections": {"close_wait": 0, "syn_recv": 0},
        "dns_healthy": True, "dns_latency_ms": 1.0,
    })}
    alerts = r.evaluate(snaps)
    drop = [a for a in alerts if "Drop" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(drop) >= 1


def test_network_syn_recv_warn():
    r = _router()
    snaps = {"network": make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {"close_wait": 0, "syn_recv": 150},
        "dns_healthy": True, "dns_latency_ms": 1.0,
    })}
    alerts = r.evaluate(snaps)
    syn = [a for a in alerts if "SYN" in a.title]
    assert len(syn) >= 1


# ─── Process ───────────────────────────────────────────────────────────────────

def test_process_zombie_warn():
    r = _router()
    snaps = {"process": make_snapshot("process", {
        "zombie": 8, "disk_sleep_procs": [],
    })}
    alerts = r.evaluate(snaps)
    z = [a for a in alerts if "Zombie" in a.title]
    assert len(z) >= 1
    assert z[0].severity == AlertSeverity.WARN


def test_process_zombie_critical():
    r = _router()
    snaps = {"process": make_snapshot("process", {
        "zombie": 25, "disk_sleep_procs": [],
    })}
    alerts = r.evaluate(snaps)
    z = [a for a in alerts if "Zombie" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(z) >= 1


def test_process_disk_sleep_warn():
    r = _router()
    # Default disk_sleep_warn is 5 — a sustained backlog, not a single transient D.
    snaps = {"process": make_snapshot("process", {
        "zombie": 0,
        "disk_sleep_procs": [{"pid": i, "name": "dd", "cmdline": "dd"} for i in range(6)],
    })}
    alerts = r.evaluate(snaps)
    ds = [a for a in alerts if "Disk-Sleep" in a.title and a.severity == AlertSeverity.WARN]
    assert len(ds) >= 1


# ─── System Events ─────────────────────────────────────────────────────────────

def test_system_events_failed_unit():
    r = _router()
    snaps = {"system_events": make_snapshot("system_events", {
        "oom_kill_count_new": 0,
        "failed_systemd_units": [{"unit": "myapp.service", "active": "failed", "sub": "failed"}],
        "dmesg_errors_new": [],
        "psi_available": False,
        "psi": {},
    })}
    alerts = r.evaluate(snaps)
    unit = [a for a in alerts if "myapp.service" in a.title]
    assert len(unit) >= 1
    assert unit[0].severity == AlertSeverity.CRITICAL


def test_system_events_oom_emergency():
    r = _router()
    snaps = {"system_events": make_snapshot("system_events", {
        "oom_kill_count_new": 1,
        "failed_systemd_units": [],
        "dmesg_errors_new": [],
        "psi_available": False,
        "psi": {},
    })}
    alerts = r.evaluate(snaps)
    oom = [a for a in alerts if a.severity == AlertSeverity.EMERGENCY]
    assert len(oom) >= 1


# ─── CPU steal / iowait (supplemental) ────────────────────────────────────────

def test_cpu_iowait_critical():
    r = _router()
    snaps = {"cpu": make_snapshot("cpu", {
        "percent_total": 50.0, "times_steal": 0.0, "times_iowait": 65.0,
        "load_avg_normalized_1m": 0.5,
    })}
    alerts = r.evaluate(snaps)
    iow = [a for a in alerts if "I/O Wait" in a.title and a.severity == AlertSeverity.CRITICAL]
    assert len(iow) >= 1


def test_cpu_steal_warn():
    r = _router()
    snaps = {"cpu": make_snapshot("cpu", {
        "percent_total": 20.0, "times_steal": 8.0, "times_iowait": 0.0,
        "load_avg_normalized_1m": 0.5,
    })}
    alerts = r.evaluate(snaps)
    steal = [a for a in alerts if "Steal" in a.title and a.severity == AlertSeverity.WARN]
    assert len(steal) >= 1


def test_no_alerts_when_all_normal():
    r = _router()
    snaps = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 10.0, "times_steal": 0.0, "times_iowait": 5.0,
            "load_avg_normalized_1m": 0.5,
        }),
        "memory": make_snapshot("memory", {
            "percent_used": 40.0, "swap_percent": 5.0,
            "swap_sout_per_sec": 0.0, "dirty_ratio_percent": 2.0,
            "fd_percent_used": 20.0, "oom_kill_count_new": 0,
        }),
    }
    alerts = r.evaluate(snaps)
    assert alerts == []


# ─── Error snapshot skipped ────────────────────────────────────────────────────

def test_error_snapshot_skipped():
    from guardian.collector.base import MetricSnapshot
    import time
    r = _router()
    snap = MetricSnapshot(collector_name="cpu", timestamp=time.time(),
                          metrics={}, status="error", error="boom")
    alerts = r.evaluate({"cpu": snap})
    cpu_alerts = [a for a in alerts if a.category == "cpu"]
    assert cpu_alerts == []


# ─── PSI evaluation ────────────────────────────────────────────────────────────

def test_psi_memory_full_critical():
    r = _router()
    snaps = {"system_events": make_snapshot("system_events", {
        "oom_kill_count_new": 0,
        "failed_systemd_units": [],
        "dmesg_errors_new": [],
        "psi_available": True,
        "psi": {
            "memory": {"full_avg10": 20.0, "some_avg10": 5.0},
            "io": {"full_avg10": 0.0, "some_avg10": 0.0},
            "cpu": {"some_avg10": 0.0},
        },
    })}
    alerts = r.evaluate(snaps)
    psi = [a for a in alerts if "PSI" in a.title or "Pressure" in a.title or "Stall" in a.title]
    assert len(psi) >= 1
