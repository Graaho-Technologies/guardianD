from __future__ import annotations

from unittest.mock import MagicMock

from guardian.alerter.base import AlertSeverity
from guardian.alerter.telegram import TelegramAlerter
from guardian.config.schema import TelegramConfig

from .conftest import make_alert


def _tg_config(**kwargs):
    cfg = TelegramConfig()
    cfg.enabled = True
    cfg.bot_token = "TEST_TOKEN"
    cfg.chat_id = "123456"
    cfg.min_severity = "WARN"
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_telegram_sends_message(mocker):
    mock_post = mocker.patch("requests.post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {}

    alerter = TelegramAlerter(_tg_config())
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))

    assert result is True
    call_args = mock_post.call_args
    payload = call_args[1]["json"]
    assert payload["chat_id"] == "123456"
    assert "parse_mode" in payload


def test_telegram_disabled():
    alerter = TelegramAlerter(TelegramConfig(enabled=False))
    result = alerter.send(make_alert())
    assert result is False


def test_telegram_below_min_severity(mocker):
    mock_post = mocker.patch("requests.post")
    cfg = _tg_config(min_severity="CRITICAL")
    alerter = TelegramAlerter(cfg)
    result = alerter.send(make_alert(AlertSeverity.WARN))
    assert result is False
    mock_post.assert_not_called()


def test_telegram_handles_error(mocker):
    mocker.patch("requests.post", side_effect=Exception("network error"))
    alerter = TelegramAlerter(_tg_config())
    result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False
