from __future__ import annotations

import pytest

from guardian.config.loader import ConfigError, load_config, validate_config, generate_default_config
from guardian.config.schema import GuardianConfig


def test_load_valid_config(tmp_path):
    config_file = tmp_path / "guardian.yaml"
    config_file.write_text("""
instance_name: test-host
environment: staging
alerts:
  slack:
    enabled: true
    webhook_url: https://hooks.slack.com/test
    min_severity: WARN
""")
    cfg = load_config(str(config_file))
    assert cfg.instance_name == "test-host"
    assert cfg.environment == "staging"
    assert cfg.alerts.slack.enabled is True
    assert cfg.alerts.slack.webhook_url == "https://hooks.slack.com/test"


def test_load_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "guardian.yaml"
    config_file.write_text("""
alerts:
  slack:
    enabled: true
    webhook_url: original-url
""")
    monkeypatch.setenv("GUARDIAN_SLACK_WEBHOOK", "https://env-override-url")
    cfg = load_config(str(config_file))
    assert cfg.alerts.slack.webhook_url == "https://env-override-url"


def test_validate_slack_missing_url():
    cfg = GuardianConfig()
    cfg.alerts.slack.enabled = True
    cfg.alerts.slack.webhook_url = ""
    errors = validate_config(cfg)
    assert any("webhook_url" in e for e in errors)


def test_validate_telegram_missing_fields():
    cfg = GuardianConfig()
    cfg.alerts.telegram.enabled = True
    cfg.alerts.telegram.bot_token = ""
    cfg.alerts.telegram.chat_id = ""
    errors = validate_config(cfg)
    assert any("bot_token" in e for e in errors)
    assert any("chat_id" in e for e in errors)


def test_validate_no_channel_enabled():
    cfg = GuardianConfig()
    errors = validate_config(cfg)
    assert any("least one" in e for e in errors)


def test_validate_interval_too_low():
    cfg = GuardianConfig()
    cfg.alerts.slack.enabled = True
    cfg.alerts.slack.webhook_url = "https://hooks.slack.com/x"
    cfg.collector.interval_seconds = 3
    errors = validate_config(cfg)
    assert any("interval" in e for e in errors)


def test_validate_warn_must_be_less_than_critical():
    cfg = GuardianConfig()
    cfg.alerts.slack.enabled = True
    cfg.alerts.slack.webhook_url = "https://hooks.slack.com/x"
    cfg.thresholds.cpu_warn = 95.0
    cfg.thresholds.cpu_critical = 80.0
    errors = validate_config(cfg)
    assert any("cpu_warn" in e for e in errors)


def test_generate_default_config(tmp_path):
    out = str(tmp_path / "default.yaml")
    generate_default_config(out)
    import os
    assert os.path.exists(out)
    import yaml
    with open(out) as f:
        data = yaml.safe_load(f)
    assert "alerts" in data
    assert "collector" in data
