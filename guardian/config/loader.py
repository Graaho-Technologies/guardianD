from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

import yaml

from .schema import (
    AlertConfig,
    APIConfig,
    AppHealthCheck,
    CollectorConfig,
    EmailConfig,
    GuardianConfig,
    IntelligenceConfig,
    PrometheusConfig,
    SlackConfig,
    StorageConfig,
    TelegramConfig,
    ThresholdConfig,
    WebhookConfig,
)

_reload_lock = threading.Lock()


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

    for key in ("instance_name", "environment"):
        if key in raw:
            setattr(config, key, raw[key])

    if "collector" in raw:
        _merge(config.collector, raw["collector"])
    if "thresholds" in raw:
        _merge(config.thresholds, raw["thresholds"])
    if "storage" in raw:
        _merge(config.storage, raw["storage"])
    if "api" in raw:
        _merge(config.api, raw["api"])
    if "prometheus" in raw:
        _merge(config.prometheus, raw["prometheus"])
    if "intelligence" in raw:
        intel_raw = raw["intelligence"]
        for key in ("enabled", "baseline_window_hours", "baseline_min_samples",
                    "warmup_minutes", "velocity_enabled", "forecast_enabled",
                    "fingerprint_enabled"):
            if key in intel_raw:
                setattr(config.intelligence, key, intel_raw[key])
        if "anomaly_collectors" in intel_raw:
            config.intelligence.anomaly_collectors = intel_raw["anomaly_collectors"]
        if "forecast_collectors" in intel_raw:
            config.intelligence.forecast_collectors = intel_raw["forecast_collectors"]

    if "alerts" in raw:
        alerts_raw = raw["alerts"]
        for key in ("cooldown_seconds", "escalation_minutes", "recovery_notifications",
                    "group_alerts", "max_alerts_per_dispatch"):
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
    if "GUARDIAN_INSTANCE_NAME" in env:
        config.instance_name = env["GUARDIAN_INSTANCE_NAME"]
    if "GUARDIAN_ENVIRONMENT" in env:
        config.environment = env["GUARDIAN_ENVIRONMENT"]


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

    if config.intelligence.baseline_min_samples < 10:
        errors.append("intelligence.baseline_min_samples must be >= 10")

    if (config.api.enabled and config.prometheus.enabled
            and config.api.port == config.prometheus.port):
        errors.append(
            f"api.port and prometheus.port cannot both be {config.api.port}"
        )

    t = config.thresholds
    pairs = [
        ("cpu_warn", "cpu_critical"),
        ("cpu_steal_warn", "cpu_steal_critical"),
        ("cpu_iowait_warn", "cpu_iowait_critical"),
        ("memory_warn", "memory_critical"),
        ("swap_warn", "swap_critical"),
        ("swap_sout_warn", "swap_sout_critical"),
        ("dirty_ratio_warn", "dirty_ratio_critical"),
        ("fd_exhaustion_warn", "fd_exhaustion_critical"),
        ("disk_warn", "disk_critical"),
        ("inode_warn", "inode_critical"),
        ("disk_await_ssd_warn_ms", "disk_await_ssd_critical_ms"),
        ("disk_await_hdd_warn_ms", "disk_await_hdd_critical_ms"),
        ("disk_await_ebs_warn_ms", "disk_await_ebs_critical_ms"),
        ("disk_await_nvme_warn_ms", "disk_await_nvme_critical_ms"),
        ("network_error_rate_warn", "network_error_rate_critical"),
        ("network_drop_rate_warn", "network_drop_rate_critical"),
        ("tcp_close_wait_warn", "tcp_close_wait_critical"),
        ("psi_cpu_some_warn", "psi_cpu_some_critical"),
        ("psi_memory_some_warn", "psi_memory_some_critical"),
        ("psi_memory_full_warn", "psi_memory_full_critical"),
        ("psi_io_some_warn", "psi_io_some_critical"),
        ("psi_io_full_warn", "psi_io_full_critical"),
        ("anomaly_zscore_warn", "anomaly_zscore_critical"),
        ("velocity_spike_warn_pct", "velocity_spike_critical_pct"),
    ]
    for warn_field, crit_field in pairs:
        warn_val = getattr(t, warn_field)
        crit_val = getattr(t, crit_field)
        if warn_val >= crit_val:
            errors.append(
                f"thresholds.{warn_field} ({warn_val}) must be < {crit_field} ({crit_val})"
            )

    # Forecast ETA: critical < warn (stricter time bound)
    if t.forecast_disk_full_critical_hours >= t.forecast_disk_full_warn_hours:
        errors.append(
            "thresholds.forecast_disk_full_critical_hours must be < forecast_disk_full_warn_hours"
        )

    return errors


def reload_config(daemon_ref: Any) -> None:
    """Hot-reload config on SIGHUP. Swaps atomically. Never crashes."""
    from ..utils.logger import get_logger
    log = get_logger(__name__)
    try:
        new_config = load_config(daemon_ref.config_path)
    except Exception as exc:
        log.error("Config reload failed, keeping old config: %s", exc)
        return

    with _reload_lock:
        old = daemon_ref.config
        # Hot-reloadable fields only
        daemon_ref.config.thresholds = new_config.thresholds
        daemon_ref.config.alerts = new_config.alerts
        daemon_ref.config.intelligence = new_config.intelligence
        daemon_ref.config.app_health_checks = new_config.app_health_checks
        daemon_ref.config.collector.interval_seconds = new_config.collector.interval_seconds
        daemon_ref.config.collector.process_top_n = new_config.collector.process_top_n
        # NOT hot-reloadable: storage.db_path, api.port, prometheus.port

    log.info("Config reloaded successfully")


def generate_default_config(path: str) -> None:
    """Write a fully-commented guardian.yaml to path."""
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
  dns_check_host: "169.254.169.253"   # AWS VPC resolver
  dns_check_fallback: "8.8.8.8"
  disk_type_detection: true

thresholds:
  # CPU
  cpu_warn: 80.0
  cpu_critical: 95.0
  cpu_steal_warn: 5.0
  cpu_steal_critical: 15.0
  cpu_iowait_warn: 40.0
  cpu_iowait_critical: 60.0
  load_avg_warn_multiplier: 2.0
  load_avg_critical_multiplier: 4.0
  # Memory
  memory_warn: 80.0
  memory_critical: 92.0
  swap_warn: 50.0
  swap_critical: 80.0
  swap_sout_warn: 10.0
  swap_sout_critical: 100.0
  dirty_ratio_warn: 10.0
  dirty_ratio_critical: 20.0
  fd_exhaustion_warn: 80.0
  fd_exhaustion_critical: 95.0
  # Disk space
  disk_warn: 85.0
  disk_critical: 95.0
  inode_warn: 85.0
  inode_critical: 95.0
  # Disk I/O latency (per type)
  disk_await_ssd_warn_ms: 10.0
  disk_await_ssd_critical_ms: 50.0
  disk_await_hdd_warn_ms: 100.0
  disk_await_hdd_critical_ms: 500.0
  disk_await_ebs_warn_ms: 20.0
  disk_await_ebs_critical_ms: 100.0
  disk_await_nvme_warn_ms: 10.0
  disk_await_nvme_critical_ms: 50.0
  # Network
  network_error_rate_warn: 0.1
  network_error_rate_critical: 1.0
  network_drop_rate_warn: 0.05
  network_drop_rate_critical: 0.5
  tcp_close_wait_warn: 100
  tcp_close_wait_critical: 500
  tcp_time_wait_warn: 1000
  tcp_syn_recv_warn: 100
  # Process
  zombie_warn: 5
  zombie_critical: 20
  # PSI — Linux 4.20+
  psi_cpu_some_warn: 30.0
  psi_cpu_some_critical: 70.0
  psi_memory_some_warn: 10.0
  psi_memory_some_critical: 30.0
  psi_memory_full_warn: 5.0
  psi_memory_full_critical: 15.0
  psi_io_some_warn: 20.0
  psi_io_some_critical: 50.0
  psi_io_full_warn: 10.0
  psi_io_full_critical: 30.0
  # Intelligence (Phase 2)
  anomaly_zscore_warn: 2.0
  anomaly_zscore_critical: 3.0
  velocity_spike_warn_pct: 40.0
  velocity_spike_critical_pct: 70.0
  forecast_disk_full_warn_hours: 8.0
  forecast_disk_full_critical_hours: 2.0

alerts:
  cooldown_seconds: 300
  escalation_minutes: 15
  recovery_notifications: true
  group_alerts: true
  max_alerts_per_dispatch: 10

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

intelligence:
  enabled: true
  baseline_window_hours: 24
  baseline_min_samples: 30
  warmup_minutes: 5
  anomaly_collectors: [cpu, memory, disk, network]
  velocity_enabled: true
  forecast_enabled: true
  forecast_collectors: [disk, memory]
  fingerprint_enabled: true

prometheus:
  enabled: false
  host: "0.0.0.0"
  port: 9732
  path: "/metrics"
  include_process_metrics: false

app_health_checks: []
# Example:
# app_health_checks:
#   - name: "my-api"
#     type: "http"
#     target: "http://localhost:8080/health"
#     interval_seconds: 30
#     timeout_seconds: 5
#     expected_status_code: 200
#     critical_on_failure: true
#     headers: {}

storage:
  log_dir: "/var/log/guardian"
  db_path: "/var/lib/guardian/metrics.db"
  log_rotation_mb: 50
  log_retention_days: 30
  metric_retention_days: 7
  baseline_retention_days: 30

api:
  enabled: true
  host: "127.0.0.1"
  port: 9731
  # Env override: GUARDIAN_API_TOKEN
  auth_token: ""
"""
    with open(path, "w") as f:
        f.write(content)
