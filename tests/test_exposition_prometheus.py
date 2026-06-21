from __future__ import annotations

"""
Tests for guardian/exposition/prometheus.py

Prometheus-client has module-level global registry state. To avoid
CollectorAlreadyRegistered errors across test runs we:
  1. Call _init_metrics() once via a session-scoped autouse fixture.
  2. Access gauge values through _metrics[key].labels(...).get() which
     returns the current value without side-effects.
  3. Never call start_http_server() in unit tests.
"""

import pytest

# ─── Guard: skip entire module if prometheus-client not installed ──────────────
pytest.importorskip("prometheus_client", reason="prometheus-client not installed")

from guardian.exposition.prometheus import (
    _PROMETHEUS_AVAILABLE,
    _init_metrics,
    _metrics,
    _metrics_initialized,
    update_metrics,
    PrometheusExpositionServer,
)
from guardian.config.schema import PrometheusConfig

from .conftest import make_snapshot


_LABEL_VALUES = {
    "instance_id": "i-test123",
    "instance_name": "test-host",
    "environment": "test",
    "aws_account_id": "123456789012",
    "aws_account_name": "test-account",
}
_LVS = ["i-test123", "test-host", "test", "123456789012", "test-account"]


@pytest.fixture(scope="module", autouse=True)
def init_prom_metrics():
    """Initialize the Prometheus registry once for the whole test module."""
    _init_metrics(include_process=False)


# ─── Availability ──────────────────────────────────────────────────────────────

def test_prometheus_available():
    assert _PROMETHEUS_AVAILABLE is True


# ─── Metric initialisation ────────────────────────────────────────────────────

def test_init_metrics_succeeds():
    result = _init_metrics()
    assert result is True


def test_init_metrics_populates_registry():
    _init_metrics()
    for key in ("g_cpu_usage", "g_mem_used", "g_mem_total", "g_swap_used",
                "g_proc_total", "g_dns_latency", "g_dns_healthy"):
        assert key in _metrics, f"expected metric key missing: {key}"


def test_init_metrics_idempotent():
    """Calling _init_metrics() twice must not raise or duplicate collectors."""
    result1 = _init_metrics()
    result2 = _init_metrics()
    assert result1 is True
    assert result2 is True


# ─── update_metrics — CPU ─────────────────────────────────────────────────────

def _gauge_get(metric_key: str, *label_vals) -> float:
    """Read the current value of a labeled Gauge via its internal _value.get()."""
    return _metrics[metric_key].labels(*label_vals)._value.get()


def test_update_metrics_cpu(mocker):
    snap = make_snapshot("cpu", {
        "percent_total": 75.0,
        "times_user": 50.0,
        "times_system": 10.0,
        "times_iowait": 5.0,
        "times_steal": 1.0,
        "times_idle": 34.0,
        "times_irq": 0.0,
        "times_softirq": 0.0,
        "load_avg_1m": 1.5,
        "load_avg_5m": 1.2,
        "load_avg_15m": 1.0,
        "load_avg_normalized_1m": 0.75,
        "load_avg_normalized_5m": 0.60,
        "load_avg_normalized_15m": 0.50,
        "freq_current_mhz": 2400.0,
        "ctx_switches_per_sec": 5000.0,
    })
    update_metrics({"cpu": snap}, _LABEL_VALUES)

    val = _gauge_get("g_cpu_usage", *_LVS)
    assert val == pytest.approx(75.0, abs=0.01)


def test_update_metrics_cpu_iowait(mocker):
    snap = make_snapshot("cpu", {
        "percent_total": 60.0,
        "times_iowait": 35.0,
        "times_steal": 0.0,
    })
    update_metrics({"cpu": snap}, _LABEL_VALUES)

    val = _gauge_get("g_cpu_iowait", *_LVS)
    assert val == pytest.approx(35.0, abs=0.01)


def test_update_metrics_cpu_steal():
    snap = make_snapshot("cpu", {
        "percent_total": 40.0,
        "times_steal": 8.5,
    })
    update_metrics({"cpu": snap}, _LABEL_VALUES)

    val = _gauge_get("g_cpu_steal", *_LVS)
    assert val == pytest.approx(8.5, abs=0.01)


# ─── update_metrics — Memory ──────────────────────────────────────────────────

def test_update_metrics_memory():
    snap = make_snapshot("memory", {
        "used_bytes": 2_000_000_000,
        "available_bytes": 6_000_000_000,
        "total_bytes": 8_000_000_000,
        "cached_bytes": 1_000_000_000,
        "buffers_bytes": 200_000_000,
        "writeback_bytes": 0,
        "percent_used": 25.0,
        "swap_used_bytes": 0,
        "swap_percent": 0.0,
        "swap_sin_per_sec": 0.0,
        "swap_sout_per_sec": 0.0,
        "dirty_bytes": 4096,
        "dirty_ratio_percent": 0.05,
        "hugepages_total": 0,
        "hugepages_free": 0,
        "fd_allocated": 512,
        "fd_max": 65536,
        "fd_percent_used": 0.78,
    })
    update_metrics({"memory": snap}, _LABEL_VALUES)

    assert _gauge_get("g_mem_used", *_LVS) == pytest.approx(2_000_000_000)
    assert _gauge_get("g_mem_total", *_LVS) == pytest.approx(8_000_000_000)


def test_update_metrics_memory_ratio():
    snap = make_snapshot("memory", {
        "percent_used": 80.0,
        "total_bytes": 8_000_000_000,
        "used_bytes": 6_400_000_000,
    })
    update_metrics({"memory": snap}, _LABEL_VALUES)

    ratio = _gauge_get("g_mem_ratio", *_LVS)
    assert ratio == pytest.approx(0.8, abs=0.01)


# ─── update_metrics — empty / missing snapshots ───────────────────────────────

def test_update_metrics_empty_snapshots():
    """Passing an empty dict must not raise."""
    update_metrics({}, _LABEL_VALUES)


def test_update_metrics_none_snapshot():
    """A None value for a collector must not raise."""
    update_metrics({"cpu": None, "memory": None}, _LABEL_VALUES)


def test_update_metrics_error_status_snapshot():
    """A snapshot with status='error' and empty metrics must not raise."""
    from guardian.collector.base import MetricSnapshot
    import time

    bad_snap = MetricSnapshot(
        collector_name="cpu", timestamp=time.time(),
        metrics={}, status="error", error="collector failed",
    )
    update_metrics({"cpu": bad_snap}, _LABEL_VALUES)


# ─── update_metrics — Disk ────────────────────────────────────────────────────

def test_update_metrics_disk_mount():
    snap = make_snapshot("disk", {
        "mounts": [{
            "device": "/dev/xvda1",
            "mountpoint": "/",
            "fstype": "ext4",
            "total_bytes": 20_000_000_000,
            "used_bytes": 10_000_000_000,
            "free_bytes": 10_000_000_000,
            "percent_used": 50.0,
            "inodes_total": 1_000_000,
            "inodes_used": 100_000,
            "inodes_free": 900_000,
            "inodes_percent": 10.0,
        }],
        "io": {},
        "total_read_bytes_per_sec": 0.0,
        "total_write_bytes_per_sec": 0.0,
        "total_iops": 0.0,
    })
    update_metrics({"disk": snap}, _LABEL_VALUES)

    ratio = _metrics["g_disk_ratio"].labels(*_LVS, "/", "/dev/xvda1", "ext4")._value.get()
    assert ratio == pytest.approx(0.5, abs=0.01)


# ─── update_metrics — Network ─────────────────────────────────────────────────

def test_update_metrics_network_dns():
    snap = make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {},
        "tcp_stats": {"retransmits_per_sec": 0.5},
        "sockstat": {"tcp_alloc": 50, "udp_inuse": 5},
        "dns_latency_ms": 12.5,
        "dns_healthy": True,
    })
    update_metrics({"network": snap}, _LABEL_VALUES)

    assert _gauge_get("g_dns_latency", *_LVS) == pytest.approx(12.5)
    assert _gauge_get("g_dns_healthy", *_LVS) == pytest.approx(1.0)


def test_update_metrics_network_dns_unhealthy():
    snap = make_snapshot("network", {
        "interfaces": {},
        "tcp_connections": {},
        "tcp_stats": {},
        "sockstat": {},
        "dns_latency_ms": -1.0,
        "dns_healthy": False,
    })
    update_metrics({"network": snap}, _LABEL_VALUES)

    assert _gauge_get("g_dns_healthy", *_LVS) == pytest.approx(0.0)


# ─── update_metrics — Process ─────────────────────────────────────────────────

def test_update_metrics_process():
    snap = make_snapshot("process", {
        "total_count": 150,
        "zombie": 2,
        "disk_sleep": 0,
        "running": 3,
        "sleeping": 145,
        "stopped": 0,
        "top_cpu": [],
        "top_memory": [],
    })
    update_metrics({"process": snap}, _LABEL_VALUES)

    assert _gauge_get("g_proc_total", *_LVS) == pytest.approx(150.0)
    assert _gauge_get("g_proc_zombie", *_LVS) == pytest.approx(2.0)


# ─── update_metrics — App Health ─────────────────────────────────────────────

def test_update_metrics_app_health():
    snap = make_snapshot("app_health", {
        "checks": [
            {
                "name": "my-api",
                "type": "http",
                "target": "http://localhost/health",
                "healthy": True,
                "latency_ms": 42.0,
                "status_code": 200,
                "error": "",
                "last_checked": 0.0,
                "consecutive_failures": 0,
            }
        ],
        "healthy_count": 1,
        "unhealthy_count": 0,
        "total_count": 1,
        "all_healthy": True,
    })
    update_metrics({"app_health": snap}, _LABEL_VALUES)

    status = _metrics["g_health_status"].labels(*_LVS, "my-api", "http")._value.get()
    assert status == pytest.approx(1.0)
    latency = _metrics["g_health_latency"].labels(*_LVS, "my-api")._value.get()
    assert latency == pytest.approx(42.0)


# ─── PrometheusExpositionServer ───────────────────────────────────────────────

def test_prometheus_server_disabled_does_not_start(mocker):
    mock_start = mocker.patch("guardian.exposition.prometheus.start_http_server")

    cfg = PrometheusConfig()
    cfg.enabled = False
    cfg.port = 9732
    cfg.host = "127.0.0.1"
    cfg.path = "/metrics"
    cfg.include_process_metrics = False

    server = PrometheusExpositionServer(cfg)
    server.start_background()

    mock_start.assert_not_called()
    assert server._started is False


def test_prometheus_server_start_background_starts_thread(mocker):
    mock_server_cls = mocker.patch("guardian.exposition.prometheus.HTTPServer")
    mock_thread_cls = mocker.patch("guardian.exposition.prometheus.threading.Thread")
    mock_thread = mock_thread_cls.return_value

    cfg = PrometheusConfig()
    cfg.enabled = True
    cfg.port = 19732
    cfg.host = "127.0.0.1"
    cfg.path = "/metrics"
    cfg.include_process_metrics = False

    server = PrometheusExpositionServer(cfg)
    server.start_background()

    mock_server_cls.assert_called_once()
    mock_thread.start.assert_called_once()
    assert server._started is True


def test_prometheus_server_start_failure_does_not_raise(mocker):
    mocker.patch(
        "guardian.exposition.prometheus.HTTPServer",
        side_effect=OSError("address in use"),
    )

    cfg = PrometheusConfig()
    cfg.enabled = True
    cfg.port = 19733
    cfg.host = "127.0.0.1"
    cfg.path = "/metrics"
    cfg.include_process_metrics = False

    server = PrometheusExpositionServer(cfg)
    server.start_background()   # must not propagate the OSError

    assert server._started is False


# ─── Graceful behaviour when not initialised ─────────────────────────────────

def test_update_metrics_without_init_no_raise():
    """
    Temporarily fake an uninitialised state and call update_metrics.
    It should silently return without raising.
    """
    import guardian.exposition.prometheus as prom_mod

    original = prom_mod._metrics_initialized
    try:
        prom_mod._metrics_initialized = False
        update_metrics({"cpu": make_snapshot("cpu", {"percent_total": 50.0})}, _LABEL_VALUES)
    finally:
        prom_mod._metrics_initialized = original
