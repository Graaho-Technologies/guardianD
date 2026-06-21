from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ThresholdConfig:
    # CPU
    cpu_warn: float = 80.0
    cpu_critical: float = 95.0
    cpu_steal_warn: float = 5.0
    cpu_steal_critical: float = 15.0
    cpu_iowait_warn: float = 40.0
    cpu_iowait_critical: float = 60.0
    load_avg_warn_multiplier: float = 2.0
    load_avg_critical_multiplier: float = 4.0

    # Memory
    memory_warn: float = 80.0
    memory_critical: float = 92.0
    swap_warn: float = 50.0
    swap_critical: float = 80.0
    swap_sout_warn: float = 10.0
    swap_sout_critical: float = 100.0
    dirty_ratio_warn: float = 10.0
    dirty_ratio_critical: float = 20.0
    fd_exhaustion_warn: float = 80.0
    fd_exhaustion_critical: float = 95.0

    # Disk space
    disk_warn: float = 85.0
    disk_critical: float = 95.0
    inode_warn: float = 85.0
    inode_critical: float = 95.0

    # Disk I/O latency — separate by disk type
    disk_await_ssd_warn_ms: float = 10.0
    disk_await_ssd_critical_ms: float = 50.0
    disk_await_hdd_warn_ms: float = 100.0
    disk_await_hdd_critical_ms: float = 500.0
    disk_await_ebs_warn_ms: float = 20.0
    disk_await_ebs_critical_ms: float = 100.0
    disk_await_nvme_warn_ms: float = 10.0
    disk_await_nvme_critical_ms: float = 50.0
    # Minimum raw I/O operations in a collection interval before disk latency
    # (await_ms = total_time / total_ops) is evaluated. await is an average over
    # very few ops on an idle disk, so a single slow I/O (one EBS fsync at 60ms)
    # reads as a CRITICAL latency spike. Below this op count, skip the eval.
    disk_await_min_ops: float = 50.0

    # Network
    network_error_rate_warn: float = 0.1
    network_error_rate_critical: float = 1.0
    network_drop_rate_warn: float = 0.05
    network_drop_rate_critical: float = 0.5
    # Minimum packets/sec (sent+recv) on an interface before its error/drop rate
    # is evaluated. error_rate = errors/total_packets, so on a near-idle iface
    # (~5 pkt/s) a single stray error reads as 20% → CRITICAL. Below this, the
    # percentage is statistical noise; skip the eval.
    network_min_pps: float = 100.0
    tcp_close_wait_warn: int = 100
    tcp_close_wait_critical: int = 500
    tcp_time_wait_warn: int = 1000
    tcp_syn_recv_warn: int = 100

    # Process
    zombie_warn: int = 5
    zombie_critical: int = 20
    # Processes in uninterruptible disk-sleep (D state). Any process doing
    # blocking I/O shows "D" momentarily — normal. Only a sustained backlog
    # indicates an I/O problem, so alert on a count, not on > 0.
    disk_sleep_warn: int = 5
    disk_sleep_critical: int = 20

    # System events
    oom_kills_critical: int = 1

    # PSI (Pressure Stall Information) — Linux 4.20+
    psi_cpu_some_warn: float = 30.0
    psi_cpu_some_critical: float = 70.0
    psi_memory_some_warn: float = 10.0
    psi_memory_some_critical: float = 30.0
    psi_memory_full_warn: float = 5.0
    psi_memory_full_critical: float = 15.0
    psi_io_some_warn: float = 20.0
    psi_io_some_critical: float = 50.0
    psi_io_full_warn: float = 10.0
    psi_io_full_critical: float = 30.0

    # Intelligence / Phase 2
    anomaly_zscore_warn: float = 2.0
    anomaly_zscore_critical: float = 3.0
    velocity_spike_warn_pct: float = 40.0
    velocity_spike_critical_pct: float = 70.0
    forecast_disk_full_warn_hours: float = 8.0
    forecast_disk_full_critical_hours: float = 2.0

    # Velocity absolute-magnitude floors (per metric path). A rate-of-change spike
    # must clear BOTH the % threshold AND this raw delta to fire. Without it, a
    # trivial swing on a tiny idle baseline (e.g. 1.2 -> 16 IOPS) reads as a
    # +1283% "CRITICAL" spike — pure noise. Units match the metric (CPU/mem are
    # percentage-points, IOPS is ops/s, TCP is connection count). 0.0 = no floor.
    velocity_min_abs_delta: Dict[str, float] = field(default_factory=lambda: {
        "cpu.percent_total": 15.0,                      # +15 percentage-points of CPU
        "memory.percent_used": 10.0,                    # +10 percentage-points of RAM
        "memory.swap_sout_per_sec": 50.0,               # +50 pages/s swapped out
        "disk.total_iops": 500.0,                       # +500 IOPS (EBS gp3 baseline is 3000)
        "network.tcp_connections.established": 100.0,   # +100 established connections
    })

    # Anomaly absolute-deviation floors (per metric path). A z-score anomaly must
    # also deviate from the baseline mean by at least this raw amount to fire.
    # Stops low-variance idle metrics (e.g. CPU bouncing 3% -> 9%) from reading as
    # a 3-sigma "CRITICAL" anomaly. Same units as the metric. 0.0 = no floor.
    anomaly_min_abs_dev: Dict[str, float] = field(default_factory=lambda: {
        "cpu.percent_total": 15.0,
        "cpu.times_iowait": 15.0,
        "cpu.times_steal": 5.0,
        "memory.percent_used": 10.0,
        "memory.swap_sout_per_sec": 50.0,
        "network.dns_latency_ms": 50.0,
    })


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
    to_addrs: List[str] = field(default_factory=list)
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
    max_alerts_per_dispatch: int = 10
    # Flap suppression. A standard threshold breach must persist for this many
    # consecutive collection cycles before it fires (debounce); a fired alert is
    # only cleared/recovered after the condition stays resolved for this many
    # consecutive cycles (hysteresis). Together they stop a metric oscillating
    # across a threshold from producing a fire/recover storm. Set both to 1 to
    # restore immediate fire/recover. Intelligence (velocity/anomaly/forecast)
    # and EMERGENCY alerts (OOM, spot termination) bypass the debounce — they are
    # single-cycle events that must fire instantly.
    breach_cycles_to_alert: int = 2
    recovery_clear_cycles: int = 2
    slack: SlackConfig = field(default_factory=SlackConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


@dataclass
class AIConfig:
    """AI-assisted alert interpretation + remediation, applied to all channels.

    Off by default. When enabled, each alert is enriched once with a short
    plain-English interpretation and concrete quick-fix steps, then rendered on
    every channel. Calls an OpenAI-compatible chat API via plain HTTP. If the
    call fails or is disabled, alerts fall back to the built-in static hints.
    """
    enabled: bool = False
    provider: str = "openai"
    api_key: str = ""                              # env override: GUARDIAN_OPENAI_API_KEY / OPENAI_API_KEY
    base_url: str = "https://api.openai.com/v1"    # override for Azure / OpenAI-compatible gateways
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 12
    max_tokens: int = 250
    include_metrics: bool = True                   # send triggering metric values for better context
    min_severity: str = "WARN"                     # only enrich alerts at/above this severity (cost control)
    cache_ttl_seconds: int = 1800                  # reuse a suggestion for a repeating alert within this window


@dataclass
class AppHealthCheck:
    name: str = ""
    type: str = "http"
    target: str = ""
    interval_seconds: int = 30
    timeout_seconds: int = 5
    expected_status_code: int = 200
    critical_on_failure: bool = True
    # Number of consecutive failed probes before an alert fires. A single
    # transient 502/timeout during a deploy, restart, or GC pause should not
    # page; require the failure to be sustained. Set to 1 for immediate alerting.
    failure_threshold: int = 2
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class StorageConfig:
    log_dir: str = "/var/log/guardian"
    db_path: str = "/var/lib/guardian/metrics.db"
    log_rotation_mb: int = 50
    log_retention_days: int = 30
    metric_retention_days: int = 7
    baseline_retention_days: int = 30


@dataclass
class APIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 9731
    auth_token: str = ""


@dataclass
class PrometheusConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 9732
    path: str = "/metrics"
    include_process_metrics: bool = False


@dataclass
class IntelligenceConfig:
    enabled: bool = True
    baseline_window_hours: int = 24
    baseline_min_samples: int = 30
    warmup_minutes: int = 5
    anomaly_collectors: List[str] = field(default_factory=lambda: [
        "cpu", "memory", "disk", "network"
    ])
    velocity_enabled: bool = True
    forecast_enabled: bool = True
    forecast_collectors: List[str] = field(default_factory=lambda: ["disk", "memory"])
    fingerprint_enabled: bool = True
    # Forecast gating. Require at least this many samples before fitting a trend
    # (short windows extrapolate noise into "will fill" alerts), and require the
    # linear fit to explain at least this fraction of variance (R²) before
    # projecting — a slope through pure noise is not a real trend.
    forecast_min_samples: int = 30
    forecast_min_r2: float = 0.9


@dataclass
class CollectorConfig:
    interval_seconds: int = 10
    process_top_n: int = 10
    ec2_imds_timeout: int = 2
    spot_interruption_check: bool = True
    # MUST be a hostname, not an IP literal — resolving an IP does no DNS query,
    # which would make the DNS health check permanently pass (see FIX-9).
    dns_check_host: str = "amazonaws.com"
    dns_check_fallback: str = "1.1.1.1"
    disk_type_detection: bool = True


@dataclass
class GuardianConfig:
    instance_name: str = ""
    environment: str = "production"
    # AWS account identity — surfaced on every alert across all channels.
    # aws_account_id is auto-detected from the IMDS instance-identity document
    # when left blank; set it explicitly to override. aws_account_name is the
    # human account alias, which is NOT exposed by IMDS, so it must be set here.
    aws_account_id: str = ""
    aws_account_name: str = ""
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
    app_health_checks: List[AppHealthCheck] = field(default_factory=list)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: APIConfig = field(default_factory=APIConfig)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
