from __future__ import annotations

import time
import uuid

import pytest

from guardian.alerter.base import Alert, AlertSeverity
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
) -> Alert:
    return Alert(
        id=str(uuid.uuid4()),
        severity=severity,
        category=category,
        title=title,
        message="Test alert message",
        metrics={"test_metric": 42},
        instance_id="i-test",
        instance_name="test-host",
        environment="test",
        timestamp=time.time(),
        fingerprint="testfingerprint",
    )


@pytest.fixture
def guardian_config() -> GuardianConfig:
    return make_config()


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()
