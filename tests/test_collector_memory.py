from __future__ import annotations

from unittest.mock import MagicMock, patch

from guardian.collector.memory import MemoryCollector


def _mock_vm():
    m = MagicMock()
    m.total = 8 * 1024**3
    m.available = 4 * 1024**3
    m.used = 4 * 1024**3
    m.free = 2 * 1024**3
    m.percent = 50.0
    m.cached = 1 * 1024**3
    m.buffers = 512 * 1024**2
    m.shared = 256 * 1024**2
    return m


def _mock_swap():
    s = MagicMock()
    s.total = 2 * 1024**3
    s.used = 512 * 1024**2
    s.free = 1536 * 1024**2
    s.percent = 25.0
    s.sin = 1000
    s.sout = 500
    return s


def test_memory_collector_returns_snapshot(mocker):
    mocker.patch("psutil.virtual_memory", return_value=_mock_vm())
    mocker.patch("psutil.swap_memory", return_value=_mock_swap())
    mocker.patch("guardian.collector.memory._parse_oom_kills", return_value=0)

    collector = MemoryCollector()
    snap = collector.collect()

    assert snap.collector_name == "memory"
    assert snap.status == "ok"
    assert snap.metrics["percent_used"] == 50.0
    assert snap.metrics["swap_percent"] == 25.0
    assert snap.metrics["oom_kills_since_boot"] == 0


def test_memory_swap_rate(mocker):
    mocker.patch("psutil.virtual_memory", return_value=_mock_vm())
    swap1 = _mock_swap()
    swap2 = _mock_swap()
    swap2.sin = 2000
    swap2.sout = 1500
    # Only one call needed — prev set manually so collect() returns swap2
    mocker.patch("psutil.swap_memory", return_value=swap2)
    mocker.patch("guardian.collector.memory._parse_oom_kills", return_value=0)

    import time
    collector = MemoryCollector()
    collector._prev_swap_sin = swap1.sin
    collector._prev_swap_sout = swap1.sout
    collector._prev_time = time.time() - 1.0

    snap = collector.collect()
    assert snap.metrics["swap_sout_per_sec"] == pytest.approx(1000.0, abs=10)


import pytest
