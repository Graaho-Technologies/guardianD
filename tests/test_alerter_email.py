from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

from guardian.alerter.base import AlertSeverity
from guardian.alerter.email_alerter import EmailAlerter
from guardian.config.schema import EmailConfig

from .conftest import make_alert


def _email_config(**kwargs):
    cfg = EmailConfig()
    cfg.enabled = True
    cfg.smtp_host = "smtp.example.com"
    cfg.smtp_port = 587
    cfg.smtp_user = "user@example.com"
    cfg.smtp_password = "password"
    cfg.from_addr = "alerts@example.com"
    cfg.to_addrs = ["oncall@example.com"]
    cfg.use_tls = True
    cfg.min_severity = "WARN"
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_email_sends(mocker):
    mock_smtp = MagicMock()
    mocker.patch("smtplib.SMTP", return_value=mock_smtp.__enter__.return_value)
    mock_smtp.__enter__.return_value.sendmail = MagicMock()

    alerter = EmailAlerter(_email_config())
    # Use context manager mock
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_instance = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is True


def test_email_disabled():
    alerter = EmailAlerter(EmailConfig(enabled=False))
    result = alerter.send(make_alert())
    assert result is False


def test_email_handles_smtp_error(mocker):
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.side_effect = smtplib.SMTPException("connect failed")
        alerter = EmailAlerter(_email_config())
        result = alerter.send(make_alert(AlertSeverity.CRITICAL))
    assert result is False


def test_email_emergency_subject(mocker):
    mock_instance = MagicMock()
    mock_smtp_cls = mocker.patch("smtplib.SMTP")
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    alerter = EmailAlerter(_email_config())
    alerter.send(make_alert(AlertSeverity.EMERGENCY))

    assert mock_instance.sendmail.called
    call_args = mock_instance.sendmail.call_args
    msg_str = call_args[0][2]
    import email.parser
    msg = email.parser.Parser().parsestr(msg_str)
    import email.header
    decoded_subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    assert "URGENT" in decoded_subject
