from __future__ import annotations

import os
from typing import Any, Dict, List

import yaml

from .schema import (
    AlertConfig,
    APIConfig,
    AppHealthCheck,
    CollectorConfig,
    EmailConfig,
    GuardianConfig,
    SlackConfig,
    StorageConfig,
    TelegramConfig,
    ThresholdConfig,
    WebhookConfig,
)


class ConfigError(Exception):
    pass


def _merge(dataclass_instance: Any, data: Dict[str, Any]) -> None:
    """Deep-merge a dict onto a dataclass in place."""
    for key, value in data.items():
        if not hasattr(dataclass_instance, key):
            continue
        current = getattr(dataclass_instance, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(dataclass_instance, key, value)


def load_config(path: str) -> GuardianConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    config = GuardianConfig()

    # Handle top-level simple fields first
    for key in ("instance_name", "environment"):
        if key in raw:
            setattr(config, key, raw[key])

    # Nested sections
    if "collector" in raw:
        _merge(config.collector, raw["collector"])
    if "thresholds" in raw:
        _merge(config.thresholds, raw["thresholds"])
    if "storage" in raw:
        _merge(config.storage, raw["storage"])
    if "api" in raw:
        _merge(config.api, raw["api"])

    if "alerts" in raw:
        alerts_raw = raw["alerts"]
        for key in ("cooldown_seconds", "escalation_minutes", "recovery_notifications", "group_alerts"):
            if key in alerts_raw:
                setattr(config.alerts, key, alerts_raw[key])
        if "slack" in alerts_raw:
            _merge(config.alerts.slack, alerts_raw["slack"])
        if "telegram" in alerts_raw:
            _merge(config.alerts.telegram, alerts_raw["telegram"])
        if "email" in alerts_raw:
            _merge(config.alerts.email, alerts_raw["email"])
        if "webhook" in alerts_raw:
            _merge(config.alerts.webhook, alerts_raw["webhook"])

    if "app_health_checks" in raw:
        checks = []
        for item in raw["app_health_checks"]:
            chk = AppHealthCheck()
            for k, v in item.items():
                if hasattr(chk, k):
                    setattr(chk, k, v)
            checks.append(chk)
        config.app_health_checks = checks

    # Environment variable overrides
    _apply_env_overrides(config)

    errors = validate_config(config)
    if errors:
        raise ConfigError("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return config


def _apply_env_overrides(config: GuardianConfig) -> None:
    env = os.environ
    if "GUARDIAN_SLACK_WEBHOOK" in env:
        config.alerts.slack.webhook_url = env["GUARDIAN_SLACK_WEBHOOK"]
    if "GUARDIAN_TELEGRAM_TOKEN" in env:
        config.alerts.telegram.bot_token = env["GUARDIAN_TELEGRAM_TOKEN"]
    if "GUARDIAN_TELEGRAM_CHAT_ID" in env:
        config.alerts.telegram.chat_id = env["GUARDIAN_TELEGRAM_CHAT_ID"]
    if "GUARDIAN_EMAIL_PASSWORD" in env:
        config.alerts.email.smtp_password = env["GUARDIAN_EMAIL_PASSWORD"]
    if "GUARDIAN_API_TOKEN" in env:
        config.api.auth_token = env["GUARDIAN_API_TOKEN"]


def validate_config(config: GuardianConfig) -> List[str]:
    errors: List[str] = []

    slack = config.alerts.slack
    if slack.enabled and not slack.webhook_url:
        errors.append("alerts.slack.enabled=true but webhook_url is empty")

    tg = config.alerts.telegram
    if tg.enabled and not tg.bot_token:
        errors.append("alerts.telegram.enabled=true but bot_token is empty")
    if tg.enabled and not tg.chat_id:
        errors.append("alerts.telegram.enabled=true but chat_id is empty")

    em = config.alerts.email
    if em.enabled:
        if not em.smtp_user:
            errors.append("alerts.email.enabled=true but smtp_user is empty")
        if not em.smtp_password:
            errors.append("alerts.email.enabled=true but smtp_password is empty")
        if not em.from_addr:
            errors.append("alerts.email.enabled=true but from_addr is empty")
        if not em.to_addrs:
            errors.append("alerts.email.enabled=true but to_addrs is empty")

    any_enabled = slack.enabled or tg.enabled or em.enabled or config.alerts.webhook.enabled
    if not any_enabled:
        errors.append("At least one alert channel must be enabled")

    if config.collector.interval_seconds < 5:
        errors.append("collector.interval_seconds must be >= 5")

    t = config.thresholds
    pairs = [
        ("cpu_warn", "cpu_critical"),
        ("memory_warn", "memory_critical"),
        ("disk_warn", "disk_critical"),
        ("swap_warn", "swap_critical"),
        ("cpu_steal_warn", "cpu_steal_critical"),
        ("disk_await_warn_ms", "disk_await_critical_ms"),
        ("tcp_close_wait_warn", "tcp_close_wait_critical"),
    ]
    for warn_field, crit_field in pairs:
        warn_val = getattr(t, warn_field)
        crit_val = getattr(t, crit_field)
        if warn_val >= crit_val:
            errors.append(f"thresholds.{warn_field} ({warn_val}) must be < {crit_field} ({crit_val})")

    return errors


def generate_default_config(path: str) -> None:
    """Write guardian.example.yaml with comments to path."""
    content = """\
# GuardianD Configuration
# All secrets can be overridden with environment variables (see comments per field)

instance_name: "prod-api-01"
environment: "production"

collector:
  interval_seconds: 10
  process_top_n: 10
  ec2_imds_timeout: 2
  spot_interruption_check: true

thresholds:
  cpu_warn: 80.0
  cpu_critical: 95.0
  cpu_steal_warn: 10.0
  cpu_steal_critical: 20.0
  memory_warn: 80.0
  memory_critical: 92.0
  swap_warn: 50.0
  swap_critical: 80.0
  load_avg_warn_multiplier: 2.0
  load_avg_critical_multiplier: 4.0
  disk_warn: 85.0
  disk_critical: 95.0
  disk_await_warn_ms: 100.0
  disk_await_critical_ms: 500.0
  tcp_close_wait_warn: 100
  tcp_close_wait_critical: 500
  network_error_rate_warn: 0.1
  oom_kills_critical: 1

alerts:
  cooldown_seconds: 300
  escalation_minutes: 15
  recovery_notifications: true
  group_alerts: true

  slack:
    enabled: false
    # Env override: GUARDIAN_SLACK_WEBHOOK
    webhook_url: "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
    channel: "#ops-alerts"
    username: "GuardianD"
    icon_emoji: ":shield:"
    min_severity: "WARN"

  telegram:
    enabled: false
    # Env override: GUARDIAN_TELEGRAM_TOKEN
    bot_token: "YOUR_BOT_TOKEN"
    # Env override: GUARDIAN_TELEGRAM_CHAT_ID
    chat_id: "YOUR_CHAT_ID"
    min_severity: "WARN"

  email:
    enabled: false
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    smtp_user: "alerts@yourcompany.com"
    # Env override: GUARDIAN_EMAIL_PASSWORD
    smtp_password: "YOUR_APP_PASSWORD"
    from_addr: "GuardianD <alerts@yourcompany.com>"
    to_addrs:
      - "oncall@yourcompany.com"
    use_tls: true
    min_severity: "CRITICAL"

  webhook:
    enabled: false
    url: "https://your-webhook-endpoint.com/guardian"
    secret: ""
    min_severity: "CRITICAL"

app_health_checks: []

storage:
  log_dir: "/var/log/guardian"
  db_path: "/var/lib/guardian/metrics.db"
  log_rotation_mb: 50
  log_retention_days: 30
  metric_retention_days: 7

api:
  enabled: true
  host: "127.0.0.1"
  port: 9731
  # Env override: GUARDIAN_API_TOKEN
  auth_token: ""
"""
    with open(path, "w") as f:
        f.write(content)
