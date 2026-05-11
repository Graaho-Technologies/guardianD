from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ThresholdConfig:
    cpu_warn: float = 80.0
    cpu_critical: float = 95.0
    memory_warn: float = 80.0
    memory_critical: float = 92.0
    disk_warn: float = 85.0
    disk_critical: float = 95.0
    swap_warn: float = 50.0
    swap_critical: float = 80.0
    load_avg_warn_multiplier: float = 2.0
    load_avg_critical_multiplier: float = 4.0
    cpu_steal_warn: float = 10.0
    cpu_steal_critical: float = 20.0
    disk_await_warn_ms: float = 100.0
    disk_await_critical_ms: float = 500.0
    tcp_close_wait_warn: int = 100
    tcp_close_wait_critical: int = 500
    network_error_rate_warn: float = 0.1
    oom_kills_critical: int = 1


@dataclass
class SlackConfig:
    enabled: bool = False
    webhook_url: str = ""
    channel: str = "#alerts"
    username: str = "GuardianD"
    icon_emoji: str = ":shield:"
    min_severity: str = "WARN"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    min_severity: str = "WARN"


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addrs: list = field(default_factory=list)
    use_tls: bool = True
    min_severity: str = "CRITICAL"


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    secret: str = ""
    min_severity: str = "CRITICAL"


@dataclass
class AlertConfig:
    cooldown_seconds: int = 300
    escalation_minutes: int = 15
    recovery_notifications: bool = True
    group_alerts: bool = True
    slack: SlackConfig = field(default_factory=SlackConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


@dataclass
class AppHealthCheck:
    name: str = ""
    type: str = "port"
    target: str = ""
    interval_seconds: int = 30
    timeout_seconds: int = 5
    expected_status_code: int = 200
    critical_on_failure: bool = True


@dataclass
class StorageConfig:
    log_dir: str = "/var/log/guardian"
    db_path: str = "/var/lib/guardian/metrics.db"
    log_rotation_mb: int = 50
    log_retention_days: int = 30
    metric_retention_days: int = 7


@dataclass
class APIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 9731
    auth_token: str = ""


@dataclass
class CollectorConfig:
    interval_seconds: int = 10
    process_top_n: int = 10
    ec2_imds_timeout: int = 2
    spot_interruption_check: bool = True


@dataclass
class GuardianConfig:
    instance_name: str = ""
    environment: str = "production"
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    app_health_checks: list = field(default_factory=list)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: APIConfig = field(default_factory=APIConfig)
