from __future__ import annotations

import json

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.alerter.webhook import WebhookAlerter
from guardian.config.schema import WebhookConfig

from .conftest import make_alert


def _webhook_config(**kwargs) -> WebhookConfig:
    cfg = WebhookConfig()
    cfg.enabled = True
    cfg.url = "https://example.com/webhook"
    cfg.secret = ""
    cfg.min_severity = "WARN"
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_webhook_sends_correct_payload(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200

    alerter = WebhookAlerter(_webhook_config())
    alert = make_alert(AlertSeverity.CRITICAL)
    result = alerter.send(alert)

    assert result is True
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    payload = json.loads(call_kwargs[1]["data"])
    assert payload["severity"] == "CRITICAL"
    assert payload["title"] == alert.title
    assert payload["fingerprint"] == alert.fingerprint
    assert "timestamp_iso" in payload
    assert payload["category"] == alert.category
    assert payload["instance_id"] == alert.instance_id


def test_webhook_disabled_returns_false():
    alerter = WebhookAlerter(_webhook_config(enabled=False))
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False


def test_webhook_with_secret_adds_header(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200

    alerter = WebhookAlerter(_webhook_config(secret="mysecret"))
    alert = make_alert(AlertSeverity.CRITICAL)
    result = alerter.send(alert)

    assert result is True
    call_kwargs = mock_post.call_args
    headers = call_kwargs[1]["headers"]
    assert "X-Guardian-Signature" in headers
    assert headers["X-Guardian-Signature"].startswith("sha256=")


def test_webhook_handles_connection_error(mocker):
    mocker.patch("requests.post", side_effect=ConnectionError("connection refused"))
    alerter = WebhookAlerter(_webhook_config())
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False


def test_webhook_below_min_severity_skipped(mocker):
    mock_post = mocker.patch("requests.post")
    alerter = WebhookAlerter(_webhook_config(min_severity="CRITICAL"))
    result = alerter.send(make_alert(AlertSeverity.WARN))
    assert result is False
    mock_post.assert_not_called()


def test_webhook_no_secret_no_signature_header(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200

    alerter = WebhookAlerter(_webhook_config(secret=""))
    alerter.send(make_alert(AlertSeverity.CRITICAL))

    call_kwargs = mock_post.call_args
    headers = call_kwargs[1]["headers"]
    assert "X-Guardian-Signature" not in headers


def test_webhook_non_2xx_returns_false(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 500
    mock_post.return_value.text = "Internal Server Error"

    alerter = WebhookAlerter(_webhook_config())
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False


def test_webhook_recovery_alert_sends(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200

    alerter = WebhookAlerter(_webhook_config(min_severity="INFO"))
    alert = make_alert(AlertSeverity.INFO)
    alert.is_recovery = True
    result = alerter.send(alert)

    assert result is True
    payload = json.loads(mock_post.call_args[1]["data"])
    assert payload["is_recovery"] is True
