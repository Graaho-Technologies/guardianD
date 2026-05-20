from __future__ import annotations

from unittest.mock import MagicMock, patch

import psutil
import pytest

from guardian.collector.process import ProcessCollector, _proc_dict


def _mock_proc(name="python3", status=psutil.STATUS_SLEEPING, pid=100, ppid=1,
               cpu_pct=5.0, mem_pct=2.0, rss=50*1024*1024):
    p = MagicMock(spec=psutil.Process)
    mem_mock = MagicMock()
    mem_mock.rss = rss
    mem_mock.vms = rss * 2
    info = {
        "pid": pid, "name": name, "cmdline": ["python3", "app.py"],
        "cpu_percent": cpu_pct, "memory_percent": mem_pct,
        "memory_info": mem_mock,
        "status": status, "num_threads": 2,
        "username": "root", "ppid": ppid, "create_time": 1700000000.0,
    }
    p.as_dict.return_value = info
    p.num_fds.return_value = 10
    return p


def test_process_collector_returns_snapshot(mocker):
    procs = [_mock_proc("nginx", psutil.STATUS_SLEEPING, 101)]
    mocker.patch("psutil.process_iter", return_value=procs)
    snap = ProcessCollector().collect()
    assert snap.collector_name == "process"
    assert snap.status == "ok"
    assert "total_count" in snap.metrics


def test_process_collector_counts_status(mocker):
    procs = [
        _mock_proc("nginx", psutil.STATUS_SLEEPING, 101),
        _mock_proc("bash",  psutil.STATUS_RUNNING,  102),
        _mock_proc("sh",    psutil.STATUS_RUNNING,  103),
    ]
    mocker.patch("psutil.process_iter", return_value=procs)
    snap = ProcessCollector().collect()
    assert snap.metrics["running"] == 2
    assert snap.metrics["sleeping"] == 1


def test_process_collector_detects_zombies(mocker):
    zombie = _mock_proc("defunct", psutil.STATUS_ZOMBIE, 200)
    mocker.patch("psutil.process_iter", return_value=[zombie])
    snap = ProcessCollector().collect()
    assert snap.metrics["zombie"] == 1
    assert len(snap.metrics["zombies"]) == 1
    assert snap.metrics["zombies"][0]["pid"] == 200


def test_process_collector_top_cpu_sorted(mocker):
    low  = _mock_proc("low",  psutil.STATUS_SLEEPING, 101, cpu_pct=1.0)
    high = _mock_proc("high", psutil.STATUS_SLEEPING, 102, cpu_pct=80.0)
    mocker.patch("psutil.process_iter", return_value=[low, high])
    snap = ProcessCollector(top_n=2).collect()
    assert snap.metrics["top_cpu"][0]["name"] == "high"


def test_process_collector_top_mem_sorted(mocker):
    small = _mock_proc("small", psutil.STATUS_SLEEPING, 101, mem_pct=1.0, rss=10*1024*1024)
    large = _mock_proc("large", psutil.STATUS_SLEEPING, 102, mem_pct=50.0, rss=200*1024*1024)
    mocker.patch("psutil.process_iter", return_value=[small, large])
    snap = ProcessCollector(top_n=2).collect()
    assert snap.metrics["top_memory"][0]["name"] == "large"


def test_process_collector_skips_vanished_process(mocker):
    good = _mock_proc("nginx", psutil.STATUS_SLEEPING, 101)
    bad = MagicMock(spec=psutil.Process)
    bad.as_dict.side_effect = psutil.NoSuchProcess(999)
    mocker.patch("psutil.process_iter", return_value=[bad, good])
    snap = ProcessCollector().collect()
    assert snap.status == "ok"
    assert snap.metrics["total_count"] >= 1


def test_process_collector_handles_error(mocker):
    mocker.patch("psutil.process_iter", side_effect=RuntimeError("boom"))
    snap = ProcessCollector().collect()
    assert snap.status == "error"


def test_proc_dict_handles_access_denied():
    p = MagicMock(spec=psutil.Process)
    p.as_dict.side_effect = psutil.AccessDenied(42)
    result = _proc_dict(p)
    assert result == {}
