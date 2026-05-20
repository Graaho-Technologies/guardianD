from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from guardian.collector.app_health import AppHealthCollector
from guardian.config.schema import AppHealthCheck


def _make_check(**kwargs) -> AppHealthCheck:
    chk = AppHealthCheck()
    chk.name = "test-check"
    chk.type = "http"
    chk.target = "http://localhost:8080/health"
    chk.interval_seconds = 0   # always run during tests
    chk.timeout_seconds = 5
    chk.expected_status_code = 200
    chk.critical_on_failure = True
    chk.headers = {}
    for k, v in kwargs.items():
        setattr(chk, k, v)
    return chk


# ─── Port checks ───────────────────────────────────────────────────────────────

def test_app_health_port_check_success(mocker):
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mocker.patch("socket.create_connection", return_value=mock_conn)

    chk = _make_check(name="port-check", type="port", target="localhost:6379")
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    assert snap.status == "ok"
    results = snap.metrics["checks"]
    assert len(results) == 1
    assert results[0]["healthy"] is True
    assert results[0]["name"] == "port-check"


def test_app_health_port_check_failure(mocker):
    mocker.patch(
        "socket.create_connection",
        side_effect=ConnectionRefusedError("connection refused"),
    )

    chk = _make_check(name="port-check", type="port", target="localhost:6379")
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is False
    assert results[0]["error"] != ""


# ─── HTTP checks ───────────────────────────────────────────────────────────────

def test_app_health_http_check_success(mocker):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mocker.patch("requests.get", return_value=mock_resp)

    chk = _make_check(name="http-check", type="http",
                      target="http://localhost:8080/health", expected_status_code=200)
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is True
    assert results[0]["status_code"] == 200


def test_app_health_http_check_wrong_status(mocker):
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mocker.patch("requests.get", return_value=mock_resp)

    chk = _make_check(name="http-check", type="http",
                      target="http://localhost:8080/health", expected_status_code=200)
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is False
    assert results[0]["status_code"] == 503
    assert "503" in results[0]["error"]


def test_app_health_http_check_connection_error(mocker):
    mocker.patch("requests.get", side_effect=ConnectionError("refused"))

    chk = _make_check(name="http-check", type="http",
                      target="http://localhost:8080/health")
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is False


# ─── Process checks ────────────────────────────────────────────────────────────

def test_app_health_process_check_found(mocker):
    mock_proc = MagicMock()
    mock_proc.name.return_value = "nginx"
    mocker.patch("psutil.process_iter", return_value=[mock_proc])

    chk = _make_check(name="nginx-proc", type="process", target="nginx")
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is True


def test_app_health_process_check_not_found(mocker):
    mock_proc = MagicMock()
    mock_proc.name.return_value = "apache2"
    mocker.patch("psutil.process_iter", return_value=[mock_proc])

    chk = _make_check(name="nginx-proc", type="process", target="nginx")
    collector = AppHealthCollector([chk])
    snap = collector.collect()

    results = snap.metrics["checks"]
    assert results[0]["healthy"] is False
    assert "nginx" in results[0]["error"]


# ─── Summary counts ────────────────────────────────────────────────────────────

def test_app_health_returns_summary_counts(mocker):
    # Two checks: one healthy (http 200), one unhealthy (http 503)
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 503

    call_count = {"n": 0}

    def fake_get(url, **kwargs):
        call_count["n"] += 1
        return mock_resp_ok if call_count["n"] == 1 else mock_resp_fail

    mocker.patch("requests.get", side_effect=fake_get)

    chk1 = _make_check(name="svc-a", type="http", target="http://a/health",
                       expected_status_code=200)
    chk2 = _make_check(name="svc-b", type="http", target="http://b/health",
                       expected_status_code=200)
    collector = AppHealthCollector([chk1, chk2])
    snap = collector.collect()

    m = snap.metrics
    assert m["total_count"] == 2
    assert m["healthy_count"] == 1
    assert m["unhealthy_count"] == 1
    assert m["all_healthy"] is False


def test_app_health_all_healthy_flag(mocker):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mocker.patch("requests.get", return_value=mock_resp)

    chk1 = _make_check(name="svc-a", type="http", target="http://a/health",
                       expected_status_code=200)
    chk2 = _make_check(name="svc-b", type="http", target="http://b/health",
                       expected_status_code=200)
    collector = AppHealthCollector([chk1, chk2])
    snap = collector.collect()

    assert snap.metrics["all_healthy"] is True


def test_app_health_empty_checks():
    collector = AppHealthCollector([])
    snap = collector.collect()
    assert snap.status == "ok"
    assert snap.metrics["total_count"] == 0
    assert snap.metrics["all_healthy"] is True


def test_app_health_consecutive_failures_tracked(mocker):
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mocker.patch("requests.get", return_value=mock_resp)

    chk = _make_check(name="failing", type="http", target="http://localhost/health",
                      expected_status_code=200)
    collector = AppHealthCollector([chk])

    # First failure
    snap1 = collector.collect()
    assert snap1.metrics["checks"][0]["consecutive_failures"] == 1

    # Second failure — interval=0 so it re-runs
    snap2 = collector.collect()
    assert snap2.metrics["checks"][0]["consecutive_failures"] == 2


def test_app_health_unknown_type_returns_unhealthy():
    chk = _make_check(name="weird", type="ftp", target="ftp://somewhere")
    collector = AppHealthCollector([chk])
    snap = collector.collect()
    results = snap.metrics["checks"]
    assert results[0]["healthy"] is False
    assert "unknown type" in results[0]["error"]
