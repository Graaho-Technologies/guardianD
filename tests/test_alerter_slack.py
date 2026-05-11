from __future__ import annotations

import json
from unittest.mock import MagicMock

from guardian.alerter.base import AlertSeverity
from guardian.alerter.slack import SlackAlerter
from guardian.config.schema import SlackConfig

from .conftest import make_alert


def _slack_config(**kwargs):
    cfg = SlackConfig()
    cfg.enabled = True
    cfg.webhook_url = "https://hooks.slack.com/test"
    cfg.min_severity = "WARN"
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_slack_sends_correct_payload(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200

    alerter = SlackAlerter(_slack_config())
    alert = make_alert(AlertSeverity.CRITICAL)
    result = alerter.send(alert)

    assert result is True
    call_kwargs = mock_post.call_args
    payload = json.loads(call_kwargs[1]["data"])
    assert "attachments" in payload


def test_slack_disabled_returns_false():
    alerter = SlackAlerter(SlackConfig(enabled=False))
    result = alerter.send(make_alert())
    assert result is False


def test_slack_below_min_severity_skipped(mocker):
    mock_post = mocker.patch("requests.post")
    cfg = _slack_config(min_severity="CRITICAL")
    alerter = SlackAlerter(cfg)
    result = alerter.send(make_alert(AlertSeverity.WARN))
    assert result is False
    mock_post.assert_not_called()


def test_slack_emergency_mentions_here(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200
    alerter = SlackAlerter(_slack_config())
    alert = make_alert(AlertSeverity.EMERGENCY)
    alerter.send(alert)
    payload = json.loads(mock_post.call_args[1]["data"])
    assert "<!here>" in payload.get("text", "")


def test_slack_handles_connection_error(mocker):
    mocker.patch("requests.post", side_effect=Exception("connection error"))
    alerter = SlackAlerter(_slack_config())
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False
