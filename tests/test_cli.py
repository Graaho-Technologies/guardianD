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


def _mock_post_response(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def test_cli_test_alert_sent(mocker, cli_runner):
    mocker.patch(
        "requests.post",
        return_value=_mock_post_response({"sent": True, "channel": "telegram",
                                          "results": {"telegram": "sent"}}),
    )
    result = cli_runner.invoke(
        cli, ["--api-url", "http://127.0.0.1:9731", "test-alert", "--channel", "telegram"]
    )
    assert result.exit_code == 0
    assert "telegram" in result.output and "sent" in result.output


def test_cli_test_alert_failed_exits_nonzero(mocker, cli_runner):
    mocker.patch(
        "requests.post",
        return_value=_mock_post_response({"sent": False, "channel": "telegram",
                                          "results": {"telegram": "failed"}}),
    )
    result = cli_runner.invoke(
        cli, ["--api-url", "http://127.0.0.1:9731", "test-alert", "--channel", "telegram"]
    )
    assert result.exit_code == 1
    assert "failed" in result.output


def test_cli_test_alert_below_severity_is_not_failure_message(mocker, cli_runner):
    # An INFO alert to a WARN-min channel is "skipped", not "failed" — but nothing
    # was delivered, so the exit code is still non-zero.
    mocker.patch(
        "requests.post",
        return_value=_mock_post_response({"sent": False, "channel": "telegram",
                                          "results": {"telegram": "skipped_below_severity"}}),
    )
    result = cli_runner.invoke(
        cli, ["--api-url", "http://127.0.0.1:9731", "test-alert",
              "--channel", "telegram", "--severity", "INFO"]
    )
    assert result.exit_code == 1
    assert "skipped" in result.output
    assert "failed" not in result.output


def test_cli_test_alert_not_enabled(mocker, cli_runner):
    mocker.patch(
        "requests.post",
        return_value=_mock_post_response({"sent": False, "channel": "slack",
                                          "results": {"slack": "not_enabled"}}),
    )
    result = cli_runner.invoke(
        cli, ["--api-url", "http://127.0.0.1:9731", "test-alert", "--channel", "slack"]
    )
    assert result.exit_code == 1
    assert "not enabled" in result.output


def test_cli_setup_openai_writes_config(mocker, cli_runner, tmp_path):
    import yaml
    cfg = tmp_path / "guardian.yaml"
    cfg.write_text("alerts:\n  telegram:\n    enabled: true\n")
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"choices": [{"message": {"content": "GuardianD AI connected."}}]}
    mocker.patch("requests.post", return_value=r)
    # prompts: api key, base url (default), model (default), min severity (default)
    result = cli_runner.invoke(
        cli, ["setup", "openai", "--config", str(cfg)], input="sk-test\n\n\n\n"
    )
    assert result.exit_code == 0, result.output
    assert "Verified" in result.output
    data = yaml.safe_load(cfg.read_text())
    assert data["ai"]["enabled"] is True
    assert data["ai"]["api_key"] == "sk-test"
    assert data["ai"]["model"] == "gpt-4o-mini"
    # existing sections are preserved
    assert data["alerts"]["telegram"]["enabled"] is True


def test_cli_setup_openai_rejected_key_does_not_write(mocker, cli_runner, tmp_path):
    cfg = tmp_path / "guardian.yaml"
    cfg.write_text("alerts:\n  telegram:\n    enabled: true\n")
    r = MagicMock()
    r.status_code = 401
    r.text = "unauthorized"
    mocker.patch("requests.post", return_value=r)
    result = cli_runner.invoke(
        cli, ["setup", "openai", "--config", str(cfg)], input="bad-key\n\n\n\n"
    )
    assert "rejected" in result.output.lower()
    assert "ai:" not in cfg.read_text()  # nothing written


def test_cli_health_command(mocker, cli_runner):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"checks": [], "healthy_count": 0, "unhealthy_count": 0, "total_count": 0}
    r.raise_for_status = MagicMock()
    mocker.patch("requests.get", return_value=r)
    result = cli_runner.invoke(cli, ["--api-url", "http://127.0.0.1:9731", "health"])
    assert result.exit_code == 0
