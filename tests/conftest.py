from __future__ import annotations

import time
import uuid

import pytest

from guardian.alerter.base import Alert, AlertSeverity, make_fingerprint
from guardian.collector.base import MetricSnapshot
from guardian.config.schema import (
    AlertConfig,
    APIConfig,
    CollectorConfig,
    GuardianConfig,
    SlackConfig,
    StorageConfig,
    TelegramConfig,
    ThresholdConfig,
)


def make_config(**kwargs) -> GuardianConfig:
    cfg = GuardianConfig()
    cfg.instance_name = "test-host"
    cfg.environment = "test"
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def make_snapshot(collector: str, metrics: dict) -> MetricSnapshot:
    return MetricSnapshot(
        collector_name=collector,
        timestamp=time.time(),
        metrics=metrics,
        status="ok",
    )


def make_alert(
    severity: AlertSeverity = AlertSeverity.WARN,
    category: str = "cpu",
    title: str = "Test Alert",
    config: GuardianConfig = None,
) -> Alert:
    cfg = config or GuardianConfig()
    return Alert(
        id=str(uuid.uuid4()),
        severity=severity,
        category=category,
        title=title,
        message="Test alert message",
        metrics={"test_metric": 42},
        instance_id="i-test",
        instance_name=cfg.instance_name or "test-host",
        environment=cfg.environment or "test",
        timestamp=time.time(),
        fingerprint=make_fingerprint(category, title),
    )


@pytest.fixture
def guardian_config() -> GuardianConfig:
    return make_config()


@pytest.fixture
def basic_config() -> GuardianConfig:
    config = GuardianConfig()
    config.instance_name = "test-instance"
    config.environment = "test"
    config.alerts.recovery_notifications = False
    return config


@pytest.fixture
def full_config(tmp_path) -> GuardianConfig:
    config = GuardianConfig()
    config.storage.db_path = str(tmp_path / "test.db")
    config.storage.log_dir = str(tmp_path / "logs")
    config.alerts.slack.enabled = True
    config.alerts.slack.webhook_url = "https://hooks.slack.com/fake"
    config.alerts.telegram.enabled = True
    config.alerts.telegram.bot_token = "fake_token"
    config.alerts.telegram.chat_id = "12345"
    config.alerts.email.enabled = True
    config.alerts.email.smtp_user = "test@example.com"
    config.alerts.email.smtp_password = "fake"
    config.alerts.email.from_addr = "test@example.com"
    config.alerts.email.to_addrs = ["dest@example.com"]
    return config


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()
