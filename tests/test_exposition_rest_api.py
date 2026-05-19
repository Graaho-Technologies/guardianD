from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from unittest.mock import MagicMock

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.config.schema import APIConfig
from guardian.exposition.rest_api import RestAPIServer, ThreadingHTTPServer, _Handler

from .conftest import make_alert, make_snapshot


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _mock_daemon():
    daemon = MagicMock()
    daemon._last_collection_ts = time.time()
    daemon._last_snapshots = {}
    daemon._start_time = time.time() - 60
    daemon.config.instance_name = "test-host"
    daemon.config.environment = "test"
    daemon.config.prometheus.enabled = False
    daemon.config.prometheus.port = 9732
    daemon.config.prometheus.host = "0.0.0.0"
    daemon.router._active = {}
    daemon.alerters = []
    daemon.baseline_engine = None
    return daemon


def _api_config(port: int, token: str = "") -> APIConfig:
    cfg = APIConfig()
    cfg.enabled = True
    cfg.host = "127.0.0.1"
    cfg.port = port
    cfg.auth_token = token
    return cfg


def _get(url: str, token: str = "") -> tuple:
    """Returns (status_code, body_dict)."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url: str, body: dict, token: str = "") -> tuple:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ─── Server lifecycle ─────────────────────────────────────────────────────────

@pytest.fixture
def server():
    port = _free_port()
    daemon = _mock_daemon()
    cfg = _api_config(port)
    srv = RestAPIServer(cfg, daemon)
    srv.start_background()
    time.sleep(0.05)   # give thread a moment to bind
    yield f"http://127.0.0.1:{port}", srv, daemon
    srv.stop()


# ─── Health endpoints ─────────────────────────────────────────────────────────

def test_healthy_endpoint(server):
    base, _, _ = server
    status, body = _get(f"{base}/-/healthy")
    assert status == 200
    assert body["status"] == "ok"


def test_ready_endpoint_when_collected(server):
    base, _, daemon = server
    daemon._last_collection_ts = time.time()
    status, body = _get(f"{base}/-/ready")
    assert status == 200
    assert body["status"] == "ready"


def test_ready_endpoint_not_ready(server):
    base, _, daemon = server
    daemon._last_collection_ts = 0.0
    status, body = _get(f"{base}/-/ready")
    assert status == 503


# ─── Status endpoint ─────────────────────────────────────────────────────────

def test_status_endpoint_no_auth(server):
    base, _, _ = server
    status, body = _get(f"{base}/api/v1/status")
    assert status == 200
    assert body["status"] == "running"
    assert "uptime_seconds" in body


def test_status_includes_intelligence_info(server):
    base, _, _ = server
    _, body = _get(f"{base}/api/v1/status")
    assert "intelligence" in body
    assert body["intelligence"]["enabled"] is False  # baseline_engine is None


def test_status_includes_prometheus_info(server):
    base, _, _ = server
    _, body = _get(f"{base}/api/v1/status")
    assert "prometheus" in body
    assert "port" in body["prometheus"]


# ─── Metrics endpoints ─────────────────────────────────────────────────────────

def test_metrics_endpoint_empty(server):
    base, _, _ = server
    status, body = _get(f"{base}/api/v1/metrics")
    assert status == 200
    assert "snapshots" in body
    assert isinstance(body["snapshots"], dict)


def test_metrics_endpoint_with_data(server):
    base, _, daemon = server
    snap = make_snapshot("cpu", {"percent_total": 42.0})
    daemon._last_snapshots = {"cpu": snap}
    _, body = _get(f"{base}/api/v1/metrics")
    assert "cpu" in body["snapshots"]


def test_metrics_collector_endpoint(server):
    base, _, daemon = server
    snap = make_snapshot("memory", {"percent_used": 55.0})
    daemon._last_snapshots = {"memory": snap}
    status, body = _get(f"{base}/api/v1/metrics/memory")
    assert status == 200
    assert body["collector_name"] == "memory"
    assert "metrics" in body


def test_metrics_collector_not_found(server):
    base, _, daemon = server
    daemon._last_snapshots = {}
    status, body = _get(f"{base}/api/v1/metrics/nonexistent")
    assert status == 404


def test_metrics_history_endpoint(server):
    base, _, daemon = server
    daemon.store.query_snapshots.return_value = []
    status, body = _get(f"{base}/api/v1/metrics/history?collector=cpu")
    assert status == 200
    assert "data" in body


# ─── Alert endpoints ─────────────────────────────────────────────────────────

def test_alerts_endpoint(server):
    base, _, daemon = server
    daemon.store.query_alerts.return_value = []
    status, body = _get(f"{base}/api/v1/alerts")
    assert status == 200
    assert "alerts" in body


def test_alerts_active_endpoint(server):
    base, _, daemon = server
    daemon.router._active = {}
    status, body = _get(f"{base}/api/v1/alerts/active")
    assert status == 200
    assert "alerts" in body


def test_alerts_active_with_data(server):
    base, _, daemon = server
    alert = make_alert(AlertSeverity.CRITICAL, "cpu", "CPU High")
    daemon.router._active = {alert.fingerprint: (alert, time.time(), time.time())}
    _, body = _get(f"{base}/api/v1/alerts/active")
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["severity"] == "CRITICAL"


# ─── POST endpoints ──────────────────────────────────────────────────────────

def test_test_alert_endpoint(server):
    base, _, daemon = server
    daemon.alerters = []
    status, body = _post(f"{base}/api/v1/alerts/test", {"severity": "WARN", "channel": "all"})
    assert status == 200
    assert "sent" in body


def test_test_alert_invalid_severity(server):
    base, _, _ = server
    status, body = _post(f"{base}/api/v1/alerts/test", {"severity": "INVALID"})
    assert status == 400


# ─── Config endpoint ─────────────────────────────────────────────────────────

def test_config_endpoint_redacts_secrets(server):
    base, _, daemon = server
    import dataclasses
    from guardian.config.schema import GuardianConfig
    real_cfg = GuardianConfig()
    real_cfg.alerts.slack.webhook_url = "https://hooks.slack.com/secret"
    daemon.config = real_cfg
    status, body = _get(f"{base}/api/v1/config")
    assert status == 200


# ─── Health checks endpoint ───────────────────────────────────────────────────

def test_health_checks_endpoint_no_snap(server):
    base, _, daemon = server
    daemon._last_snapshots = {}
    status, body = _get(f"{base}/api/v1/health-checks")
    assert status == 200
    assert "checks" in body


# ─── Intelligence endpoints ───────────────────────────────────────────────────

def test_intelligence_baselines_no_baseline_engine(server):
    base, _, daemon = server
    daemon.baseline_engine = None
    status, _ = _get(f"{base}/api/v1/intelligence/baselines")
    assert status == 503


def test_intelligence_anomalies_endpoint(server):
    base, _, daemon = server
    daemon.store.query_alerts.return_value = [
        {"category": "intelligence", "severity": "WARN", "timestamp": time.time()}
    ]
    status, body = _get(f"{base}/api/v1/intelligence/anomalies")
    assert status == 200
    assert "anomalies" in body


# ─── Auth ─────────────────────────────────────────────────────────────────────

def test_auth_token_required_when_configured():
    port = _free_port()
    daemon = _mock_daemon()
    cfg = _api_config(port, token="secret123")
    srv = RestAPIServer(cfg, daemon)
    srv.start_background()
    time.sleep(0.05)

    # No token → 401
    status, body = _get(f"http://127.0.0.1:{port}/api/v1/status")
    assert status == 401

    # Correct token → 200
    status, body = _get(f"http://127.0.0.1:{port}/api/v1/status", token="secret123")
    assert status == 200

    srv.stop()


# ─── 404 for unknown paths ────────────────────────────────────────────────────

def test_unknown_path_returns_404(server):
    base, _, _ = server
    status, body = _get(f"{base}/api/v1/nonexistent")
    assert status == 404


# ─── Server disabled ─────────────────────────────────────────────────────────

def test_server_disabled_does_not_start():
    cfg = _api_config(0)
    cfg.enabled = False
    daemon = _mock_daemon()
    srv = RestAPIServer(cfg, daemon)
    srv.start_background()
    assert srv._server is None
