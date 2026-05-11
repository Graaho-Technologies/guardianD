from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from guardian.collector.cpu import CPUCollector
from guardian.collector.base import MetricSnapshot


def _fake_cpu_stats():
    s = MagicMock()
    s.ctx_switches = 1000
    s.interrupts = 500
    return s


def test_cpu_collector_returns_snapshot(mocker):
    mocker.patch("psutil.cpu_percent", side_effect=[45.2, [10.0, 20.0, 30.0, 40.0]])
    mocker.patch("psutil.cpu_count", side_effect=[4, 2])
    mocker.patch("psutil.getloadavg", return_value=(1.5, 1.2, 1.0))
    cpu_times = MagicMock()
    cpu_times.user = 30.0
    cpu_times.system = 10.0
    cpu_times.idle = 55.0
    cpu_times.iowait = 2.0
    cpu_times.steal = 0.5
    cpu_times.softirq = 0.5
    mocker.patch("psutil.cpu_times_percent", return_value=cpu_times)
    freq = MagicMock()
    freq.current = 2400.0
    freq.max = 3200.0
    mocker.patch("psutil.cpu_freq", return_value=freq)
    mocker.patch("psutil.cpu_stats", return_value=_fake_cpu_stats())

    collector = CPUCollector()
    snap = collector.collect()

    assert isinstance(snap, MetricSnapshot)
    assert snap.collector_name == "cpu"
    assert snap.status == "ok"
    assert snap.metrics["percent_total"] == 45.2
    assert snap.metrics["count_logical"] == 4
    assert snap.metrics["load_avg_1m"] == 1.5
    assert snap.metrics["times_steal"] == 0.5
    assert snap.metrics["freq_current_mhz"] == 2400.0


def test_cpu_collector_handles_error(mocker):
    mocker.patch("psutil.cpu_percent", side_effect=Exception("test error"))
    collector = CPUCollector()
    snap = collector.collect()
    assert snap.status == "error"
    assert "test error" in snap.error


def test_cpu_collector_delta_stats(mocker):
    stats1 = MagicMock()
    stats1.ctx_switches = 1000
    stats1.interrupts = 500
    stats2 = MagicMock()
    stats2.ctx_switches = 2000
    stats2.interrupts = 1000

    mocker.patch("psutil.cpu_percent", return_value=50.0)
    mocker.patch("psutil.cpu_count", return_value=4)
    mocker.patch("psutil.getloadavg", return_value=(1.0, 1.0, 1.0))
    mocker.patch("psutil.cpu_times_percent", return_value=MagicMock(user=30.0, system=10.0, idle=60.0, iowait=0.0, steal=0.0, softirq=0.0))
    mocker.patch("psutil.cpu_freq", return_value=None)
    # Only one call needed — prev_stats set manually so collect() calls once → stats2
    mocker.patch("psutil.cpu_stats", return_value=stats2)

    import time
    collector = CPUCollector()
    collector._prev_stats = stats1
    collector._prev_time = time.time() - 1.0

    snap = collector.collect()
    assert snap.metrics["ctx_switches_per_sec"] > 0
