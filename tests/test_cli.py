from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from guardianctl.cli import cli


def _mock_status_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "status": "running",
        "uptime_seconds": 3600,
        "version": "0.1.0",
        "instance_id": "i-test",
        "instance_name": "test-host",
        "environment": "test",
        "collectors": [{"name": "cpu", "last_collected": 1700000000.0, "status": "ok"}],
        "active_alert_count": 2,
    }
    r.raise_for_status = MagicMock()
    return r


def _mock_metrics_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "snapshots": {
            "cpu": {"percent_total": 45.2},
        },
        "timestamp": 1700000000.0,
    }
    r.raise_for_status = MagicMock()
    return r


def test_cli_status_command(mocker, cli_runner):
    mocker.patch("requests.get", return_value=_mock_status_response())
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "status"])
    assert result.exit_code == 0
    assert "Running" in result.output


def test_cli_metrics_command(mocker, cli_runner):
    mocker.patch("requests.get", return_value=_mock_metrics_response())
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "metrics"])
    assert result.exit_code == 0


def test_cli_connection_refused(mocker, cli_runner):
    import requests
    mocker.patch("requests.get", side_effect=requests.exceptions.ConnectionError())
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "status"])
    assert result.exit_code == 1
    assert "Cannot connect" in result.output


def test_cli_alerts_command(mocker, cli_runner):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"alerts": [], "total": 0}
    r.raise_for_status = MagicMock()
    mocker.patch("requests.get", return_value=r)
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "alerts"])
    assert result.exit_code == 0


def test_cli_health_command(mocker, cli_runner):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"checks": [], "healthy_count": 0, "unhealthy_count": 0, "total_count": 0}
    r.raise_for_status = MagicMock()
    mocker.patch("requests.get", return_value=r)
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "health"])
    assert result.exit_code == 0
