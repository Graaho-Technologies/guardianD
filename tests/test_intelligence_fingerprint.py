from __future__ import annotations

import pytest

from guardian.config.schema import GuardianConfig, ThresholdConfig
from guardian.intelligence.fingerprint import BottleneckFingerprinter

from .conftest import make_snapshot


def _fingerprinter(**threshold_overrides) -> BottleneckFingerprinter:
    cfg = GuardianConfig()
    for k, v in threshold_overrides.items():
        setattr(cfg.thresholds, k, v)
    return BottleneckFingerprinter(cfg)


def _patterns(findings) -> list:
    return [f["pattern"] for f in findings]


# ─── cpu_bound ─────────────────────────────────────────────────────────────────

def test_fingerprint_cpu_bound():
    fp = _fingerprinter()
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 90.0,
            "times_iowait": 5.0,
            "times_steal": 0.0,
            "load_avg_normalized_1m": 3.0,
        }),
        "memory": make_snapshot("memory", {}),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert "cpu_bound" in _patterns(findings)


def test_fingerprint_cpu_bound_not_triggered_when_iowait_high():
    fp = _fingerprinter()
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 90.0,
            "times_iowait": 45.0,   # high iowait → not purely CPU-bound
            "times_steal": 0.0,
            "load_avg_normalized_1m": 3.0,
        }),
    }
    findings = fp.analyze(snapshots)
    assert "cpu_bound" not in _patterns(findings)


# ─── disk_io_bottleneck ────────────────────────────────────────────────────────

def test_fingerprint_disk_io_bottleneck():
    fp = _fingerprinter()
    # ssd_warn default = 10ms
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 60.0,
            "times_iowait": 55.0,   # > 40
            "times_steal": 0.0,
            "load_avg_normalized_1m": 1.0,
        }),
        "disk": make_snapshot("disk", {
            "io": {"sda": {"await_ms": 20.0, "disk_type": "ssd"}},  # > ssd_warn 10ms
        }),
    }
    findings = fp.analyze(snapshots)
    assert "disk_io_bottleneck" in _patterns(findings)


def test_fingerprint_disk_io_bottleneck_not_when_low_await(mocker):
    fp = _fingerprinter()
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 60.0,
            "times_iowait": 55.0,
            "times_steal": 0.0,
            "load_avg_normalized_1m": 1.0,
        }),
        "disk": make_snapshot("disk", {
            "io": {"sda": {"await_ms": 2.0, "disk_type": "ssd"}},  # < ssd_warn 10ms
        }),
    }
    findings = fp.analyze(snapshots)
    assert "disk_io_bottleneck" not in _patterns(findings)


# ─── memory_pressure ──────────────────────────────────────────────────────────

def test_fingerprint_memory_pressure():
    fp = _fingerprinter()
    # swap_sout_warn default = 10.0 pages/sec
    snapshots = {
        "memory": make_snapshot("memory", {
            "percent_used": 92.0,       # > 85
            "swap_sout_per_sec": 15.0,  # > swap_sout_warn
            "fd_percent_used": 0.0,
        }),
        "cpu": make_snapshot("cpu", {}),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert "memory_pressure" in _patterns(findings)


def test_fingerprint_memory_pressure_not_triggered_without_swap():
    fp = _fingerprinter()
    snapshots = {
        "memory": make_snapshot("memory", {
            "percent_used": 92.0,
            "swap_sout_per_sec": 0.0,   # no active swapping
            "fd_percent_used": 0.0,
        }),
    }
    findings = fp.analyze(snapshots)
    assert "memory_pressure" not in _patterns(findings)


# ─── ec2_noisy_neighbor ────────────────────────────────────────────────────────

def test_fingerprint_noisy_neighbor():
    fp = _fingerprinter()
    # cpu_steal_warn default = 5.0
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 30.0,    # < 60 → app is idle
            "times_iowait": 5.0,
            "times_steal": 10.0,     # > steal_warn 5.0
            "load_avg_normalized_1m": 0.5,
        }),
        "memory": make_snapshot("memory", {}),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert "ec2_noisy_neighbor" in _patterns(findings)


def test_fingerprint_noisy_neighbor_not_when_cpu_also_high():
    fp = _fingerprinter()
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 80.0,   # >= 60 → app IS using CPU
            "times_iowait": 5.0,
            "times_steal": 10.0,
            "load_avg_normalized_1m": 1.0,
        }),
    }
    findings = fp.analyze(snapshots)
    assert "ec2_noisy_neighbor" not in _patterns(findings)


# ─── No findings ──────────────────────────────────────────────────────────────

def test_fingerprint_no_findings():
    fp = _fingerprinter()
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 20.0,
            "times_iowait": 2.0,
            "times_steal": 0.0,
            "load_avg_normalized_1m": 0.3,
        }),
        "memory": make_snapshot("memory", {
            "percent_used": 40.0,
            "swap_sout_per_sec": 0.0,
            "fd_percent_used": 10.0,
        }),
        "disk": make_snapshot("disk", {"io": {}}),
        "network": make_snapshot("network", {"tcp_connections": {"close_wait": 0}}),
    }
    findings = fp.analyze(snapshots)
    assert findings == []


# ─── Sorting by confidence ────────────────────────────────────────────────────

def test_fingerprint_sorted_by_confidence():
    fp = _fingerprinter()
    # Trigger cpu_bound (borderline confidence) + memory_pressure (high confidence)
    snapshots = {
        "cpu": make_snapshot("cpu", {
            "percent_total": 82.0,   # just above 80 → low confidence
            "times_iowait": 5.0,
            "times_steal": 0.0,
            "load_avg_normalized_1m": 2.0,
        }),
        "memory": make_snapshot("memory", {
            "percent_used": 99.0,   # very high → high confidence
            "swap_sout_per_sec": 50.0,
            "fd_percent_used": 0.0,
        }),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert len(findings) >= 2
    confidences = [f["confidence"] for f in findings]
    assert confidences == sorted(confidences, reverse=True)


# ─── connection_leak ──────────────────────────────────────────────────────────

def test_fingerprint_connection_leak():
    fp = _fingerprinter()
    # tcp_close_wait_warn default = 100
    snapshots = {
        "network": make_snapshot("network", {
            "tcp_connections": {"close_wait": 200},  # > 100
        }),
        "cpu": make_snapshot("cpu", {}),
        "memory": make_snapshot("memory", {}),
        "disk": make_snapshot("disk", {}),
    }
    findings = fp.analyze(snapshots)
    assert "connection_leak" in _patterns(findings)


def test_fingerprint_no_connection_leak_below_threshold():
    fp = _fingerprinter()
    snapshots = {
        "network": make_snapshot("network", {
            "tcp_connections": {"close_wait": 50},  # < 100
        }),
    }
    findings = fp.analyze(snapshots)
    assert "connection_leak" not in _patterns(findings)


# ─── fd_starvation ────────────────────────────────────────────────────────────

def test_fingerprint_fd_starvation():
    fp = _fingerprinter()
    # fd_exhaustion_warn default = 80.0
    snapshots = {
        "memory": make_snapshot("memory", {
            "percent_used": 50.0,
            "swap_sout_per_sec": 0.0,
            "fd_percent_used": 90.0,   # > 80
        }),
        "cpu": make_snapshot("cpu", {}),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert "fd_starvation" in _patterns(findings)


# ─── Missing snapshots graceful ───────────────────────────────────────────────

def test_fingerprint_missing_snapshots_no_crash():
    fp = _fingerprinter()
    findings = fp.analyze({})
    assert isinstance(findings, list)


def test_fingerprint_error_snapshot_skipped():
    from guardian.collector.base import MetricSnapshot
    import time

    fp = _fingerprinter()
    snapshots = {
        "cpu": MetricSnapshot(
            collector_name="cpu", timestamp=time.time(),
            metrics={}, status="error", error="collector failed",
        ),
    }
    # Should not crash and should return empty list (no triggering conditions met)
    findings = fp.analyze(snapshots)
    assert isinstance(findings, list)


# ─── psi_memory_stall ─────────────────────────────────────────────────────────

def test_fingerprint_psi_memory_stall():
    fp = _fingerprinter()
    # psi_memory_full_warn default = 5.0
    snapshots = {
        "system_events": make_snapshot("system_events", {
            "psi_available": True,
            "psi": {
                "memory": {"full_avg10": 10.0},  # > 5.0 warn
            },
        }),
        "cpu": make_snapshot("cpu", {}),
        "memory": make_snapshot("memory", {}),
        "disk": make_snapshot("disk", {}),
        "network": make_snapshot("network", {}),
    }
    findings = fp.analyze(snapshots)
    assert "psi_memory_stall" in _patterns(findings)
