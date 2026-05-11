# CLAUDE.md — GuardianD Implementation Specification

> **To Claude Code**: This document is your complete, authoritative implementation guide for `guardianD` — a production-grade EC2 instance observability daemon. Read every section before writing any code. Follow the structure exactly. Do not improvise the package layout, config schema, or CLI interface. All decisions have been made deliberately.

---

## 0. Project Overview

**guardianD** is a Python daemon that runs as a `systemd` service on AWS EC2 instances. It:

- Continuously collects system and EC2-specific resource metrics
- Detects anomalies, threshold breaches, and critical system events
- Routes alerts to Slack, Telegram, and Email with severity-based logic
- Exposes a `guardianctl` CLI for operators to control the daemon, inspect status, query metrics, and send test alerts
- Persists logs and metrics locally in structured JSON + rotating files + SQLite

**Phases in scope**: Phase 1 (core daemon + collector + alerts + systemd) and Phase 4 (guardianctl CLI + REST API).

**Not in scope now**: Phase 2 (intelligence/ML layer), Phase 3 (Prometheus/Grafana). But **stub the interfaces** so they can be added without refactoring.

---

## 1. Repository Layout

Create exactly this structure. No deviations.

```
guardianD/
├── CLAUDE.md                        ← this file (keep in repo)
├── README.md
├── pyproject.toml
├── setup.cfg
├── MANIFEST.in
├── .gitignore
│
├── guardian/                        ← main package
│   ├── __init__.py                  ← version = "0.1.0"
│   ├── main.py                      ← daemon entrypoint
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py                ← YAML config loader + validator
│   │   └── schema.py                ← dataclasses for config sections
│   │
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── base.py                  ← abstract BaseCollector
│   │   ├── cpu.py
│   │   ├── memory.py
│   │   ├── disk.py
│   │   ├── network.py
│   │   ├── process.py
│   │   ├── ec2.py                   ← EC2-specific (IMDS, CPU credits, spot notice)
│   │   ├── system_events.py         ← dmesg, OOM kills, systemd failed units
│   │   └── app_health.py            ← port liveness, HTTP checks, process existence
│   │
│   ├── alerter/
│   │   ├── __init__.py
│   │   ├── base.py                  ← abstract BaseAlerter
│   │   ├── router.py                ← severity routing, dedup, cooldown, escalation
│   │   ├── slack.py
│   │   ├── telegram.py
│   │   ├── email_alerter.py
│   │   └── webhook.py               ← generic webhook (stub for Phase 4 extension)
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sqlite_store.py          ← metric time-series storage
│   │   └── log_writer.py            ← rotating structured JSON logs
│   │
│   ├── exposition/
│   │   ├── __init__.py
│   │   ├── prometheus.py            ← STUB: /metrics HTTP endpoint (Phase 3)
│   │   └── rest_api.py              ← REST API for guardianctl (Phase 4, implement now)
│   │
│   ├── intelligence/
│   │   ├── __init__.py
│   │   └── stub.py                  ← STUB only: anomaly detector interface (Phase 2)
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py                ← internal guardian logger (not the metric logger)
│       ├── platform.py              ← OS/EC2 environment detection helpers
│       └── retry.py                 ← retry decorator with backoff
│
├── guardianctl/
│   ├── __init__.py
│   └── cli.py                       ← Click-based CLI (guardianctl command)
│
├── config/
│   └── guardian.example.yaml        ← fully commented example config
│
├── systemd/
│   └── guardian.service             ← systemd unit file
│
├── scripts/
│   ├── install.sh                   ← one-liner install script
│   └── uninstall.sh
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_collector_cpu.py
    ├── test_collector_memory.py
    ├── test_collector_disk.py
    ├── test_collector_network.py
    ├── test_collector_ec2.py
    ├── test_alerter_router.py
    ├── test_alerter_slack.py
    ├── test_alerter_telegram.py
    ├── test_alerter_email.py
    ├── test_storage_sqlite.py
    ├── test_config_loader.py
    └── test_cli.py
```

---

## 2. `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=65", "wheel"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "guardiand"
version = "0.1.0"
description = "Production-grade EC2 instance observability daemon"
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.9"
authors = [{ name = "Mobasshir Bhuiya Shagor" }]

dependencies = [
    "psutil>=5.9.0",
    "requests>=2.28.0",
    "PyYAML>=6.0",
    "click>=8.1.0",
    "rich>=13.0.0",
    "tabulate>=0.9.0",
    "python-dateutil>=2.8.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov",
    "pytest-mock",
    "black",
    "ruff",
    "mypy",
]

[project.scripts]
guardiand = "guardian.main:cli_entry"
guardianctl = "guardianctl.cli:cli"

[tool.setuptools.packages.find]
where = ["."]
include = ["guardian*", "guardianctl*"]

[tool.black]
line-length = 100
target-version = ["py39"]

[tool.ruff]
line-length = 100
target-version = "py39"
```

---

## 3. Configuration Schema — `guardian/config/schema.py`

Use Python `dataclasses` throughout. No Pydantic (keep deps minimal).

```python
# All config sections as dataclasses.
# Loader will instantiate these from YAML.

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
    load_avg_warn_multiplier: float = 2.0      # warn if load_1m > cpu_count * multiplier
    load_avg_critical_multiplier: float = 4.0
    cpu_steal_warn: float = 10.0               # EC2-specific
    cpu_steal_critical: float = 20.0
    disk_await_warn_ms: float = 100.0          # disk latency
    disk_await_critical_ms: float = 500.0
    tcp_close_wait_warn: int = 100             # connection leak indicator
    tcp_close_wait_critical: int = 500
    network_error_rate_warn: float = 0.1       # errors/total packets %
    oom_kills_critical: int = 1                # any OOM kill = critical immediately

@dataclass
class SlackConfig:
    enabled: bool = False
    webhook_url: str = ""
    channel: str = "#alerts"
    username: str = "GuardianD"
    icon_emoji: str = ":shield:"
    min_severity: str = "WARN"                 # INFO | WARN | CRITICAL | EMERGENCY

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
    cooldown_seconds: int = 300               # don't repeat same alert within this window
    escalation_minutes: int = 15              # escalate WARN to CRITICAL if not recovered
    recovery_notifications: bool = True
    group_alerts: bool = True                 # bundle multiple issues in one message
    slack: SlackConfig = field(default_factory=SlackConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)

@dataclass
class AppHealthCheck:
    name: str
    type: str                                 # "port" | "http" | "process" | "systemd_service"
    target: str                               # host:port, URL, process_name, or service_name
    interval_seconds: int = 30
    timeout_seconds: int = 5
    expected_status_code: int = 200           # for HTTP only
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
    auth_token: str = ""                      # bearer token, empty = no auth (localhost only)

@dataclass
class CollectorConfig:
    interval_seconds: int = 10
    process_top_n: int = 10                   # track top N processes by CPU/mem
    ec2_imds_timeout: int = 2                 # fast timeout for non-EC2 hosts
    spot_interruption_check: bool = True

@dataclass
class GuardianConfig:
    instance_name: str = ""                   # human label, defaults to EC2 instance ID or hostname
    environment: str = "production"           # production | staging | dev
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    app_health_checks: list = field(default_factory=list)   # list of AppHealthCheck
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: APIConfig = field(default_factory=APIConfig)
```

---

## 4. Config Loader — `guardian/config/loader.py`

```python
# Requirements:
# - load_config(path: str) -> GuardianConfig
# - Deep-merge YAML dict onto dataclass defaults (YAML values override defaults)
# - Support environment variable overrides for secrets:
#     GUARDIAN_SLACK_WEBHOOK, GUARDIAN_TELEGRAM_TOKEN, GUARDIAN_TELEGRAM_CHAT_ID,
#     GUARDIAN_EMAIL_PASSWORD, GUARDIAN_API_TOKEN
# - validate_config(config: GuardianConfig) -> list[str] (list of validation errors)
#   Validation rules:
#     - If slack.enabled, webhook_url must not be empty
#     - If telegram.enabled, bot_token and chat_id must not be empty
#     - If email.enabled, smtp_user, smtp_password, from_addr, to_addrs must not be empty
#     - At least one alert channel must be enabled
#     - collector.interval_seconds must be >= 5
#     - All threshold warn values must be < critical values
# - Raise ConfigError (custom exception) with all validation errors if any fail
# - generate_default_config(path: str) -> None  writes guardian.example.yaml with comments
```

---

## 5. Collector Layer — `guardian/collector/`

### `base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class MetricSnapshot:
    collector_name: str
    timestamp: float                    # unix timestamp
    metrics: dict[str, Any]            # flat or nested, collector-defined
    status: str = "ok"                 # "ok" | "error" | "partial"
    error: str = ""

class BaseCollector(ABC):
    """All collectors implement this interface."""
    
    name: str = "base"
    
    @abstractmethod
    def collect(self) -> MetricSnapshot:
        """Collect metrics and return a snapshot. Must never raise — catch internally."""
        ...
    
    def is_available(self) -> bool:
        """Return False if this collector cannot run on this host (e.g., EC2 on bare metal)."""
        return True
```

### `cpu.py` — Implement these exact metrics:

```python
# MetricSnapshot.metrics keys:
{
    "percent_total": float,                  # overall CPU %
    "percent_per_core": list[float],         # per-core %
    "count_logical": int,
    "count_physical": int,
    "load_avg_1m": float,
    "load_avg_5m": float,
    "load_avg_15m": float,
    "load_avg_normalized_1m": float,         # load_1m / cpu_count
    "times_user": float,                     # % time in user space
    "times_system": float,
    "times_idle": float,
    "times_iowait": float,                   # critical: high = disk bottleneck
    "times_steal": float,                    # critical: high = EC2 noisy neighbor
    "times_softirq": float,
    "freq_current_mhz": float,
    "freq_max_mhz": float,
    "ctx_switches_per_sec": float,           # delta from last collect
    "interrupts_per_sec": float,
}
# Use psutil.cpu_percent(percpu=True), psutil.getloadavg(), psutil.cpu_times_percent()
# For ctx_switches and interrupts: compute per-second delta between collections
# Store previous psutil.cpu_stats() result in instance variable
```

### `memory.py` — Implement these exact metrics:

```python
{
    "total_bytes": int,
    "available_bytes": int,
    "used_bytes": int,
    "free_bytes": int,
    "percent_used": float,
    "cached_bytes": int,
    "buffers_bytes": int,
    "shared_bytes": int,
    "swap_total_bytes": int,
    "swap_used_bytes": int,
    "swap_free_bytes": int,
    "swap_percent": float,
    "swap_sin_per_sec": float,               # swap-in rate (delta)
    "swap_sout_per_sec": float,              # swap-out rate (delta) — high = memory pressure
    "oom_kills_since_boot": int,             # parse /proc/vmstat: oom_kill field
}
```

### `disk.py` — Implement these exact metrics:

```python
# Per mount point (list of dicts):
{
    "mounts": [
        {
            "device": str,
            "mountpoint": str,
            "fstype": str,
            "total_bytes": int,
            "used_bytes": int,
            "free_bytes": int,
            "percent_used": float,
            "inodes_total": int,
            "inodes_used": int,
            "inodes_free": int,
            "inodes_percent": float,
        }
    ],
    # Per-disk I/O (from psutil.disk_io_counters(perdisk=True)):
    "io": {
        "<disk_name>": {
            "read_bytes_per_sec": float,
            "write_bytes_per_sec": float,
            "read_ops_per_sec": float,
            "write_ops_per_sec": float,
            "read_latency_ms": float,        # read_time / read_count
            "write_latency_ms": float,
            "await_ms": float,               # combined average latency — KEY METRIC
            "busy_percent": float,           # busy_time / elapsed * 100
        }
    }
}
# All I/O metrics are deltas — store previous counters in instance variable
# Skip pseudo filesystems: tmpfs, devtmpfs, squashfs, overlay (container layers)
```

### `network.py` — Implement these exact metrics:

```python
{
    "interfaces": {
        "<iface_name>": {
            "bytes_sent_per_sec": float,
            "bytes_recv_per_sec": float,
            "packets_sent_per_sec": float,
            "packets_recv_per_sec": float,
            "errors_in_per_sec": float,
            "errors_out_per_sec": float,
            "drops_in_per_sec": float,
            "drops_out_per_sec": float,
            "error_rate_percent": float,     # (errors_in + errors_out) / total_packets * 100
            "is_up": bool,
            "speed_mbps": int,
            "mtu": int,
        }
    },
    "tcp_connections": {
        "established": int,
        "time_wait": int,
        "close_wait": int,                   # KEY: high = connection leak in app
        "listen": int,
        "total": int,
    },
    "tcp_stats": {
        "retransmits_per_sec": float,        # from /proc/net/snmp: RetransSegs delta
        "resets_per_sec": float,
    },
    "dns_latency_ms": float,                 # resolve a known host (e.g., amazon.com) and time it
}
# All rate metrics are deltas
# For DNS latency: use socket.getaddrinfo with timeout, catch exceptions gracefully
# Skip loopback (lo) interface from main metrics but include in TCP connection counts
```

### `process.py` — Implement these exact metrics:

```python
{
    "total_count": int,
    "running": int,
    "sleeping": int,
    "zombie": int,                           # zombie processes = critical indicator
    "top_cpu": [                             # top N by CPU (N from config)
        {
            "pid": int,
            "name": str,
            "cmdline": str,                  # truncated to 100 chars
            "cpu_percent": float,
            "memory_percent": float,
            "memory_rss_bytes": int,
            "status": str,
            "threads": int,
            "open_files": int,
            "username": str,
        }
    ],
    "top_memory": [...],                     # same structure, top N by memory
    "zombie_list": [{"pid": int, "name": str, "ppid": int}],
}
# Use psutil.process_iter() with attrs=['pid','name','cmdline','cpu_percent',
#   'memory_percent','memory_info','status','num_threads','username','ppid']
# Handle psutil.NoSuchProcess, psutil.AccessDenied gracefully
# cmdline: join with space, truncate to 100 chars
```

### `ec2.py` — Implement these exact metrics:

```python
# IMDS base URL: http://169.254.169.254
# All IMDS calls must have timeout from config (default 2s)
# If IMDS unreachable, set is_ec2 = False and return minimal snapshot

{
    "is_ec2": bool,
    "instance_id": str,
    "instance_type": str,
    "availability_zone": str,
    "region": str,
    "ami_id": str,
    "public_ip": str,
    "private_ip": str,
    "hostname": str,
    "iam_role": str,                         # from IMDS /iam/security-credentials/
    
    # CPU credits (T-series only — empty dict for other types)
    "cpu_credits": {
        "balance": float,                    # from CloudWatch? No — parse /sys/fs/cgroup or skip
        # Note: CPU credit balance is NOT available from IMDS. 
        # Implement a stub that returns {} for now.
        # Leave a TODO comment for CloudWatch integration in Phase 2.
    },
    
    # Spot interruption (only if config.collector.spot_interruption_check)
    "spot_interruption": {
        "scheduled": bool,                   # True if termination notice found
        "action": str,                       # "terminate" | "stop" | "hibernate" | ""
        "notice_time": str,                  # ISO timestamp or ""
    },
    
    # Instance metadata v2 token (IMDSv2)
    # Always use IMDSv2: first PUT to /latest/api/token with TTL header
}

# IMDS endpoints to call:
# PUT http://169.254.169.254/latest/api/token  (Header: X-aws-ec2-metadata-token-ttl-seconds: 21600)
# GET http://169.254.169.254/latest/meta-data/instance-id
# GET http://169.254.169.254/latest/meta-data/instance-type
# GET http://169.254.169.254/latest/meta-data/placement/availability-zone
# GET http://169.254.169.254/latest/meta-data/placement/region
# GET http://169.254.169.254/latest/meta-data/ami-id
# GET http://169.254.169.254/latest/meta-data/public-ipv4
# GET http://169.254.169.254/latest/meta-data/local-ipv4
# GET http://169.254.169.254/latest/meta-data/hostname
# GET http://169.254.169.254/latest/meta-data/spot/termination-time  (404 = no interruption)
# GET http://169.254.169.254/latest/meta-data/spot/instance-action
```

### `system_events.py` — Implement these exact metrics:

```python
{
    "dmesg_errors": [                        # last 50 error/warn lines from dmesg
        {
            "timestamp": float,
            "level": str,                    # "err" | "warn" | "crit" | "alert" | "emerg"
            "message": str,
        }
    ],
    "oom_kills": [                           # OOM kill events from dmesg
        {
            "timestamp": float,
            "process_name": str,
            "pid": int,
            "message": str,
        }
    ],
    "oom_kill_count_new": int,               # OOM kills since last collection cycle
    "failed_systemd_units": [               # systemctl --failed output parsed
        {
            "unit": str,
            "load": str,
            "active": str,
            "sub": str,
            "description": str,
        }
    ],
    "kernel_version": str,
    "uptime_seconds": float,
    "boot_time": float,                      # unix timestamp
    "last_reboot_reason": str,               # best-effort from last dmesg lines
}

# dmesg: use subprocess(['dmesg', '--level=err,warn,crit,alert,emerg', '--time-format=iso', '-T'])
# Handle dmesg permission errors gracefully (may need CAP_SYSLOG or run as root)
# systemctl: use subprocess(['systemctl', '--failed', '--no-legend', '--plain'])
# Parse OOM kills by looking for "Out of memory: Kill process" in dmesg output
# Track which OOM events were already seen using a set of (timestamp, pid) tuples
```

### `app_health.py` — Implement these exact checks:

```python
# Takes list[AppHealthCheck] from config

{
    "checks": [
        {
            "name": str,
            "type": str,                     # "port" | "http" | "process" | "systemd_service"
            "target": str,
            "healthy": bool,
            "latency_ms": float,
            "status_code": int,              # HTTP only, 0 otherwise
            "error": str,                    # empty if healthy
            "last_checked": float,           # unix timestamp
        }
    ],
    "healthy_count": int,
    "unhealthy_count": int,
    "total_count": int,
}

# Port check: socket.create_connection((host, port), timeout=config.timeout)
# HTTP check: requests.get(url, timeout=config.timeout) check status_code
# Process check: iterate psutil.process_iter(), match by name
# Systemd check: subprocess(['systemctl', 'is-active', service_name])
```

---

## 6. Alert Router — `guardian/alerter/router.py`

This is the most complex component. Implement precisely.

```python
# AlertSeverity enum:
class AlertSeverity(Enum):
    INFO = 0
    WARN = 1
    CRITICAL = 2
    EMERGENCY = 3

# Alert dataclass:
@dataclass
class Alert:
    id: str                                  # uuid4
    severity: AlertSeverity
    category: str                            # "cpu" | "memory" | "disk" | "network" | 
                                             # "process" | "ec2" | "system_event" | "app_health"
    title: str
    message: str                             # human-readable explanation
    metrics: dict                            # raw metric values that triggered this
    instance_id: str
    instance_name: str
    environment: str
    timestamp: float
    fingerprint: str                         # hash of (category + title) for dedup

# AlertRouter class:
class AlertRouter:
    def __init__(self, config: GuardianConfig, alerters: list[BaseAlerter]):
        ...
    
    def evaluate(self, snapshots: dict[str, MetricSnapshot]) -> list[Alert]:
        """
        Takes all collector snapshots, evaluates thresholds, returns new alerts.
        Called after every collection cycle.
        """
    
    def dispatch(self, alerts: list[Alert]) -> None:
        """
        Routes each alert to appropriate channels based on severity and cooldown.
        Handles dedup, grouping, and escalation tracking.
        """
    
    def _check_recovery(self, snapshots: dict[str, MetricSnapshot]) -> list[Alert]:
        """
        For any previously fired alert, check if the condition has resolved.
        If resolved and config.alerts.recovery_notifications, fire a recovery alert.
        """

# Threshold evaluation rules — implement ALL of these:
# 
# CPU:
#   cpu.percent_total >= thresholds.cpu_warn            → WARN "High CPU Usage"
#   cpu.percent_total >= thresholds.cpu_critical        → CRITICAL "Critical CPU Usage"
#   cpu.times_steal >= thresholds.cpu_steal_warn        → WARN "EC2 CPU Steal Detected"
#   cpu.times_steal >= thresholds.cpu_steal_critical    → CRITICAL "Severe EC2 CPU Steal"
#   cpu.times_iowait >= 60.0                            → WARN "High I/O Wait"
#   cpu.load_avg_normalized_1m >= load_avg_warn_mult    → WARN "High System Load"
#   cpu.load_avg_normalized_1m >= load_avg_crit_mult    → CRITICAL "Critical System Load"
#
# Memory:
#   memory.percent_used >= thresholds.memory_warn       → WARN "High Memory Usage"
#   memory.percent_used >= thresholds.memory_critical   → CRITICAL "Critical Memory Usage"
#   memory.swap_percent >= thresholds.swap_warn         → WARN "Swap Usage Elevated"
#   memory.swap_percent >= thresholds.swap_critical     → CRITICAL "Heavy Swap Activity"
#   memory.swap_sout_per_sec > 100                      → WARN "Active Swap-Out Detected"
#   memory.oom_kill_count_new >= 1                      → EMERGENCY "OOM Kill Detected"
#
# Disk (per mount):
#   disk.percent_used >= thresholds.disk_warn           → WARN "Disk Space Warning: {mount}"
#   disk.percent_used >= thresholds.disk_critical       → CRITICAL "Disk Space Critical: {mount}"
#   disk.inodes_percent >= thresholds.disk_warn         → WARN "Inode Exhaustion Warning: {mount}"
#   disk.inodes_percent >= thresholds.disk_critical     → CRITICAL "Inode Exhaustion Critical: {mount}"
# Per disk I/O:
#   disk.io.await_ms >= thresholds.disk_await_warn_ms   → WARN "High Disk Latency: {disk}"
#   disk.io.await_ms >= thresholds.disk_await_crit_ms   → CRITICAL "Critical Disk Latency: {disk}"
#
# Network:
#   network.tcp_connections.close_wait >= tcp_close_wait_warn    → WARN "TCP CLOSE_WAIT Buildup"
#   network.tcp_connections.close_wait >= tcp_close_wait_crit    → CRITICAL "Severe Connection Leak"
#   network.tcp_connections.zombie >= 1                           → WARN (per process)
#   per-iface error_rate_percent >= network_error_rate_warn       → WARN "Network Errors: {iface}"
#
# System Events:
#   any new OOM kill                                              → EMERGENCY (already handled above)
#   any failed_systemd_units                                      → CRITICAL "Systemd Unit Failed: {unit}"
#   new kernel error in dmesg (level in err/crit/alert/emerg)    → CRITICAL "Kernel Error Detected"
#
# EC2:
#   spot_interruption.scheduled == True                           → EMERGENCY "SPOT TERMINATION IN 2 MINUTES"
#
# App Health:
#   any check.healthy == False AND check.critical_on_failure      → CRITICAL "Health Check Failed: {name}"
#   any check.healthy == False AND NOT check.critical_on_failure  → WARN "Health Check Failed: {name}"
#
# Dedup logic:
#   fingerprint = sha256(category + "|" + title)[:16]
#   self._active_alerts: dict[str, tuple[Alert, float]]  # fingerprint → (alert, first_seen_ts)
#   If fingerprint in active_alerts AND (now - last_sent) < config.alerts.cooldown_seconds:
#       skip (don't resend)
#   If fingerprint in active_alerts AND severity escalated: always send
#
# Escalation logic:
#   If fingerprint in active_alerts AND severity == WARN:
#       AND (now - first_seen_ts) > config.alerts.escalation_minutes * 60:
#       upgrade to CRITICAL and send
#
# Recovery logic:
#   When a metric returns below its warn threshold:
#       remove from active_alerts
#       if config.alerts.recovery_notifications: fire INFO alert "Recovered: {title}"
```

---

## 7. Alert Channel Implementations

### `guardian/alerter/slack.py`

```python
# Use Slack Incoming Webhooks (no SDK needed, just requests.post)
# 
# Message format — use Slack Block Kit:
# - Header block: severity emoji + title
# - Section block: message text
# - Fields block: instance_name, environment, timestamp (formatted)
# - Context block: metric key-values that triggered the alert (max 10 fields)
#
# Severity emojis:
#   INFO → :information_source:
#   WARN → :warning:
#   CRITICAL → :rotating_light:
#   EMERGENCY → :sos:
#
# Colors (attachment color):
#   INFO → "#36a64f" (green)
#   WARN → "#ffcc00" (yellow)
#   CRITICAL → "#e01e5a" (red)
#   EMERGENCY → "#7c0000" (dark red)
#
# For EMERGENCY alerts, @here mention in the text field
# 
# Implement send(alert: Alert) -> bool
# Return True on success (2xx response), False on failure
# Log failure to guardian internal logger, never raise
```

### `guardian/alerter/telegram.py`

```python
# Use Telegram Bot API: POST https://api.telegram.org/bot{token}/sendMessage
# Parse mode: Markdown
#
# Message format:
# {severity_emoji} *{severity}* — {title}
# 
# {message}
# 
# 📍 *Instance*: {instance_name}
# 🌍 *Environment*: {environment}
# 🕐 *Time*: {timestamp_human}
# 
# *Metrics:*
# `{key}`: {value}
# `{key}`: {value}
# ...
#
# Severity emojis: INFO=ℹ️  WARN=⚠️  CRITICAL=🚨  EMERGENCY=🆘
# 
# Implement send(alert: Alert) -> bool
# Telegram API endpoint: https://api.telegram.org/bot{token}/sendMessage
# Payload: {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
# Handle Telegram 429 rate limit with retry-after header
```

### `guardian/alerter/email_alerter.py`

```python
# Use smtplib + email.mime (stdlib only)
# 
# Email format: HTML multipart
# Subject: [{severity}] {title} — {instance_name} ({environment})
# 
# HTML body structure:
# - Header bar with severity color
# - Instance info table (name, environment, region, instance_type, timestamp)
# - Alert message paragraph
# - Metrics table (key | value) for all triggering metrics
# - Footer: "Sent by GuardianD v{version}"
# 
# For EMERGENCY: subject prefix "🆘 URGENT:"
# 
# HTML template: build with f-strings or basic string template (no Jinja2 dep needed here)
# but if Jinja2 is available (it is, it's in deps), use a template string defined in the module
# 
# Implement send(alert: Alert) -> bool
# Connect → starttls → login → sendmail → quit
# Handle SMTPException gracefully, log and return False
```

### `guardian/alerter/base.py`

```python
from abc import ABC, abstractmethod

class BaseAlerter(ABC):
    name: str = "base"
    
    @abstractmethod
    def send(self, alert: Alert) -> bool:
        ...
    
    def is_enabled(self) -> bool:
        return True
    
    def meets_severity_threshold(self, alert: Alert, min_severity: str) -> bool:
        """Return True if alert.severity >= configured min_severity for this channel."""
        ...
```

---

## 8. Storage Layer

### `guardian/storage/sqlite_store.py`

```python
# Schema — create these tables on init:
#
# CREATE TABLE IF NOT EXISTS metric_snapshots (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     collector_name TEXT NOT NULL,
#     timestamp REAL NOT NULL,
#     metrics_json TEXT NOT NULL,         -- JSON blob
#     status TEXT NOT NULL DEFAULT 'ok'
# );
# CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON metric_snapshots(timestamp);
# CREATE INDEX IF NOT EXISTS idx_snapshots_collector ON metric_snapshots(collector_name, timestamp);
#
# CREATE TABLE IF NOT EXISTS alerts (
#     id TEXT PRIMARY KEY,               -- uuid4
#     fingerprint TEXT NOT NULL,
#     severity TEXT NOT NULL,
#     category TEXT NOT NULL,
#     title TEXT NOT NULL,
#     message TEXT NOT NULL,
#     metrics_json TEXT NOT NULL,
#     timestamp REAL NOT NULL,
#     sent_to TEXT NOT NULL DEFAULT ''   -- comma-separated channel names that succeeded
# );
# CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp);
# CREATE INDEX IF NOT EXISTS idx_alerts_fp ON alerts(fingerprint, timestamp);
#
# Methods to implement:
#   insert_snapshot(snapshot: MetricSnapshot) -> None
#   insert_alert(alert: Alert, sent_to: list[str]) -> None
#   query_snapshots(collector: str, since: float, until: float) -> list[dict]
#   query_alerts(since: float, until: float, severity: str = None) -> list[dict]
#   latest_snapshot(collector: str) -> dict | None
#   prune_old_data(retention_days: int) -> int    # returns rows deleted
#
# Connection: use thread-local connections (threading.local())
# WAL mode: PRAGMA journal_mode=WAL (for concurrent reads from API)
```

### `guardian/storage/log_writer.py`

```python
# Two log files, both rotated:
# 1. guardian.log — human-readable structured logs (for operators)
# 2. guardian.jsonl — JSON Lines (one JSON object per line, for ingestion)
#
# guardian.log format per entry:
# 2024-01-15 14:32:10.123 [CRITICAL] [cpu] High CPU Usage — cpu_total=96.2% steal=0.1%
#
# guardian.jsonl format per entry:
# {"ts": 1705327930.123, "severity": "CRITICAL", "category": "cpu", "title": "...", "metrics": {...}}
#
# Use Python logging.handlers.RotatingFileHandler
# Max bytes: config.storage.log_rotation_mb * 1024 * 1024
# Backup count: 10
#
# Methods:
#   log_alert(alert: Alert) -> None
#   log_snapshot(snapshot: MetricSnapshot) -> None   # only at DEBUG level / jsonl
#   log_event(level: str, message: str, **kwargs) -> None   # general daemon events
```

---

## 9. Main Daemon — `guardian/main.py`

```python
# GuardianDaemon class — the main orchestrator

class GuardianDaemon:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self.collectors: list[BaseCollector] = self._init_collectors()
        self.alerters: list[BaseAlerter] = self._init_alerters()
        self.router = AlertRouter(self.config, self.alerters)
        self.store = SQLiteStore(self.config.storage)
        self.log_writer = LogWriter(self.config.storage)
        self.api_server = RestAPIServer(self.config.api, self)  # Phase 4
        self._running = False
        self._last_snapshots: dict[str, MetricSnapshot] = {}
        self._heartbeat_file = "/var/run/guardian/guardian.heartbeat"
    
    def start(self) -> None:
        """Main daemon loop."""
        self._setup_signal_handlers()
        self._write_pid_file()
        self.api_server.start_background()    # start REST API in a thread
        self._running = True
        
        while self._running:
            cycle_start = time.time()
            
            try:
                snapshots = self._collect_all()
                self._store_snapshots(snapshots)
                alerts = self.router.evaluate(snapshots)
                self.router.dispatch(alerts)
                self._write_heartbeat()
                self._run_maintenance_if_due()
            except Exception as e:
                # Never let collection cycle crash the daemon
                self.log_writer.log_event("ERROR", f"Collection cycle error: {e}")
            
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.config.collector.interval_seconds - elapsed)
            time.sleep(sleep_time)
    
    def stop(self) -> None:
        self._running = False
        self.api_server.stop()
        self.log_writer.log_event("INFO", "GuardianD stopped gracefully")
    
    def _collect_all(self) -> dict[str, MetricSnapshot]:
        """Run all collectors concurrently using ThreadPoolExecutor."""
        # Use concurrent.futures.ThreadPoolExecutor(max_workers=len(collectors))
        # Timeout each collector at interval_seconds - 2
        ...
    
    def _write_heartbeat(self) -> None:
        """Write current unix timestamp to heartbeat file. Used by watchdog."""
        ...
    
    def _run_maintenance_if_due(self) -> None:
        """Every 24h: prune old data from SQLite and old log files."""
        ...
    
    def _setup_signal_handlers(self) -> None:
        """SIGTERM and SIGINT → graceful stop. SIGHUP → reload config."""
        ...
    
    def _write_pid_file(self) -> None:
        """Write PID to /var/run/guardian/guardian.pid"""
        ...

# cli_entry() — the console_scripts entrypoint for `guardiand`
def cli_entry():
    """Parses --config, --daemon (fork to background), --version flags."""
    ...
```

---

## 10. REST API — `guardian/exposition/rest_api.py`

**No Flask/FastAPI.** Use Python stdlib `http.server.HTTPServer` with a custom handler. Keep it lightweight.

Implement these endpoints exactly:

```
GET  /api/v1/status
     → {"status": "running", "uptime_seconds": N, "version": "0.1.0",
        "instance_id": "...", "instance_name": "...", "environment": "...",
        "collectors": [{"name": "cpu", "last_collected": ts, "status": "ok"},...],
        "last_collection_ts": ts}

GET  /api/v1/metrics
     → {"snapshots": {collector_name: {metrics dict}, ...}, "timestamp": ts}

GET  /api/v1/metrics/{collector_name}
     → single collector's latest MetricSnapshot as JSON

GET  /api/v1/metrics/history?collector=cpu&since=<ts>&until=<ts>&limit=100
     → {"data": [{...snapshot...}, ...]}

GET  /api/v1/alerts?since=<ts>&severity=CRITICAL&limit=50
     → {"alerts": [{...alert...}, ...], "total": N}

GET  /api/v1/alerts/active
     → {"alerts": [{...alert...}, ...]}   # currently active (not recovered) alerts

POST /api/v1/alerts/test
     Body: {"severity": "WARN", "channel": "slack"}
     → sends a test alert to the specified channel
     → {"sent": true, "channel": "slack"}

GET  /api/v1/health-checks
     → latest app health check results

POST /api/v1/control/reload
     → trigger config reload (SIGHUP)
     → {"status": "reloading"}

GET  /api/v1/config
     → current config as JSON (redact all secrets/passwords/tokens)

# Auth: if config.api.auth_token is set, require Authorization: Bearer {token} header
# on all endpoints. Return 401 otherwise.
# If auth_token is empty, only accept connections from 127.0.0.1.
#
# Run in a daemon thread (daemon=True) so it doesn't block shutdown
# Content-Type: application/json on all responses
# CORS: not needed (localhost only by default)
```

---

## 11. CLI — `guardianctl/cli.py`

Use `click` for argument parsing and `rich` for output formatting.

```python
# All commands use the REST API (requests to config.api.host:port)
# Read config path from env GUARDIAN_CONFIG or default /etc/guardian/guardian.yaml
# Read API token from env GUARDIAN_API_TOKEN or config file

@click.group()
@click.option('--config', default='/etc/guardian/guardian.yaml', envvar='GUARDIAN_CONFIG')
@click.option('--api-url', default=None, envvar='GUARDIAN_API_URL')
@click.pass_context
def cli(ctx, config, api_url): ...

# --- Daemon management ---

@cli.command('start')
# Start the daemon (calls systemctl start guardian or runs in foreground with --foreground)
@click.option('--foreground', '-f', is_flag=True)
@click.option('--config', default='/etc/guardian/guardian.yaml')

@cli.command('stop')
# Stop the daemon (systemctl stop guardian)

@cli.command('restart')
# Restart the daemon

@cli.command('status')
# Rich output: daemon status, uptime, version, collection stats, active alert count
# Use rich.table.Table and rich.panel.Panel
# Example output:
# ┌─ GuardianD Status ──────────────────────────────────┐
# │ Status:    ● Running (PID 1234)                     │
# │ Uptime:    2d 14h 32m                               │
# │ Version:   0.1.0                                    │
# │ Instance:  prod-api-01 (i-0abc123def456789)         │
# │ Environment: production                             │
# │ Active Alerts: 2 (1 CRITICAL, 1 WARN)              │
# └─────────────────────────────────────────────────────┘
# Collectors: [table with name, last_collected, status, latency]

# --- Metrics ---

@cli.command('metrics')
@click.option('--collector', '-c', default=None, help='Specific collector name')
@click.option('--watch', '-w', is_flag=True, help='Refresh every N seconds')
@click.option('--interval', '-i', default=5)
# Display current metrics in a rich formatted table
# --watch: use rich.live.Live for live updating display
# Default: show summary table of all collectors
# With --collector cpu: show detailed CPU metrics

@cli.command('top')
# Like 'metrics' but shows a top-like live view:
# Updates every 2s, shows: CPU%, Mem%, Disk%, Load, Net I/O, Top Processes
# Use rich.live.Live + rich.layout.Layout

# --- Alerts ---

@cli.command('alerts')
@click.option('--since', default=None, help='ISO datetime or relative like "1h", "24h"')
@click.option('--severity', default=None, type=click.Choice(['INFO','WARN','CRITICAL','EMERGENCY']))
@click.option('--limit', default=50)
@click.option('--active', is_flag=True, help='Show only active (unresolved) alerts')
# Rich table with colored severity, timestamp, title, category

@cli.command('test-alert')
@click.option('--channel', type=click.Choice(['slack','telegram','email','all']), default='all')
@click.option('--severity', type=click.Choice(['INFO','WARN','CRITICAL','EMERGENCY']), default='WARN')
# Fire a test alert to specified channel(s) via POST /api/v1/alerts/test

# --- Health Checks ---

@cli.command('health')
# Show app health check results in a rich table
# Color: green for healthy, red for unhealthy

# --- Configuration ---

@cli.command('config')
@click.argument('subcommand', type=click.Choice(['show', 'validate', 'reload']))
# show: pretty-print current config (redacted secrets)
# validate: validate config file and report errors
# reload: POST /api/v1/control/reload

@cli.command('init')
@click.option('--output', default='./guardian.yaml')
# Generate a fully commented guardian.yaml config file

@cli.command('install')
@click.option('--config', default='/etc/guardian/guardian.yaml')
@click.option('--systemd/--no-systemd', default=True)
# Install systemd service, create dirs (/etc/guardian, /var/log/guardian, 
# /var/lib/guardian, /var/run/guardian), copy config
# Requires root (check effective UID)

@cli.command('uninstall')
# Remove systemd service, optionally remove data dirs

# --- Logging ---

@cli.command('logs')
@click.option('--follow', '-f', is_flag=True)
@click.option('--lines', '-n', default=50)
@click.option('--level', default=None, type=click.Choice(['INFO','WARN','CRITICAL','EMERGENCY']))
# Tail /var/log/guardian/guardian.log
# --follow: like tail -f, using rich.console for colored output
# Filter by level if provided
```

---

## 12. Systemd Unit — `systemd/guardian.service`

```ini
[Unit]
Description=GuardianD — EC2 Instance Observability Daemon
Documentation=https://github.com/your-org/guardiand
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/local/bin/guardiand --config /etc/guardian/guardian.yaml
ExecReload=/bin/kill -HUP $MAINPID

# Restart policy — this daemon MUST NOT stay down
Restart=always
RestartSec=5
StartLimitInterval=60
StartLimitBurst=5

# Environment
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/guardian/guardian.env    # optional secrets file (mode 600)

# Resource limits
LimitNOFILE=65536
LimitNPROC=512

# Logging (also goes to /var/log/guardian/)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=guardiand

# Security hardening
ProtectSystem=false                            # needs read access to /proc, /sys
ProtectHome=read-only
NoNewPrivileges=false                          # needs to read dmesg (CAP_SYSLOG)

# PID file
PIDFile=/var/run/guardian/guardian.pid
RuntimeDirectory=guardian
RuntimeDirectoryMode=0755

[Install]
WantedBy=multi-user.target
```

---

## 13. Example Config — `config/guardian.example.yaml`

```yaml
# GuardianD Configuration
# All secrets can be overridden with environment variables (see comments per field)
# Env var override format: GUARDIAN_<SECTION>_<KEY> (uppercase, underscores)

# Human-readable name for this instance (defaults to EC2 instance ID or hostname)
instance_name: "prod-api-01"

# Environment tag (appears in all alerts)
environment: "production"

collector:
  # How often to collect metrics (seconds, minimum 5)
  interval_seconds: 10
  # How many top processes to track by CPU and memory
  process_top_n: 10
  # Timeout for EC2 IMDS calls (set low — if not on EC2 this fails fast)
  ec2_imds_timeout: 2
  # Poll for EC2 Spot interruption notices
  spot_interruption_check: true

thresholds:
  # CPU thresholds (percent)
  cpu_warn: 80.0
  cpu_critical: 95.0
  # EC2 CPU steal (percent stolen by hypervisor — noisy neighbor indicator)
  cpu_steal_warn: 10.0
  cpu_steal_critical: 20.0
  # Memory thresholds (percent)
  memory_warn: 80.0
  memory_critical: 92.0
  # Swap thresholds (percent)
  swap_warn: 50.0
  swap_critical: 80.0
  # Load average multipliers (alert when load_1m > cpu_count * multiplier)
  load_avg_warn_multiplier: 2.0
  load_avg_critical_multiplier: 4.0
  # Disk usage (percent)
  disk_warn: 85.0
  disk_critical: 95.0
  # Disk I/O latency (milliseconds)
  disk_await_warn_ms: 100.0
  disk_await_critical_ms: 500.0
  # TCP CLOSE_WAIT connections (connection leak indicator)
  tcp_close_wait_warn: 100
  tcp_close_wait_critical: 500
  # Network error rate (percent of total packets)
  network_error_rate_warn: 0.1
  # OOM kills (any = critical immediately)
  oom_kills_critical: 1

alerts:
  # Don't repeat the same alert within this window (seconds)
  cooldown_seconds: 300
  # Escalate a WARN to CRITICAL if not recovered within this time (minutes)
  escalation_minutes: 15
  # Send recovery notifications when issues resolve
  recovery_notifications: true
  # Bundle multiple simultaneous alerts into one message
  group_alerts: true

  slack:
    enabled: false
    # Env override: GUARDIAN_SLACK_WEBHOOK
    webhook_url: "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
    channel: "#ops-alerts"
    username: "GuardianD"
    icon_emoji: ":shield:"
    # Minimum severity to send to this channel: INFO | WARN | CRITICAL | EMERGENCY
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
      - "devops-lead@yourcompany.com"
    use_tls: true
    min_severity: "CRITICAL"

  webhook:
    enabled: false
    url: "https://your-webhook-endpoint.com/guardian"
    secret: ""
    min_severity: "CRITICAL"

# Application health checks (add one entry per service you want monitored)
app_health_checks:
  - name: "Web App (HTTP)"
    type: "http"
    target: "http://localhost:8080/health"
    interval_seconds: 30
    timeout_seconds: 5
    expected_status_code: 200
    critical_on_failure: true

  - name: "PostgreSQL (Port)"
    type: "port"
    target: "localhost:5432"
    interval_seconds: 30
    timeout_seconds: 3
    critical_on_failure: true

  - name: "Nginx (Process)"
    type: "process"
    target: "nginx"
    interval_seconds: 60
    critical_on_failure: true

  - name: "App Worker (Systemd)"
    type: "systemd_service"
    target: "myapp-worker"
    interval_seconds: 60
    critical_on_failure: true

storage:
  # Directory for log files
  log_dir: "/var/log/guardian"
  # SQLite database path
  db_path: "/var/lib/guardian/metrics.db"
  # Max log file size before rotation (MB)
  log_rotation_mb: 50
  # How long to keep log files (days)
  log_retention_days: 30
  # How long to keep metric data in SQLite (days)
  metric_retention_days: 7

api:
  # Enable the REST API (used by guardianctl)
  enabled: true
  # Bind address (keep 127.0.0.1 unless you need remote access)
  host: "127.0.0.1"
  # Port for the REST API
  port: 9731
  # Bearer token for auth (leave empty for no auth — localhost only is still secure)
  # Env override: GUARDIAN_API_TOKEN
  auth_token: ""
```

---

## 14. Install Script — `scripts/install.sh`

```bash
#!/usr/bin/env bash
# GuardianD install script
# Usage: curl -sSL https://... | bash
# Or: bash scripts/install.sh [--config /path/to/config.yaml]

set -euo pipefail

GUARDIAN_USER="root"
CONFIG_DIR="/etc/guardian"
LOG_DIR="/var/log/guardian"
DATA_DIR="/var/lib/guardian"
RUN_DIR="/var/run/guardian"
SYSTEMD_DIR="/etc/systemd/system"

# 1. Check Python >= 3.9
# 2. pip install guardiand (or pip install -e . if running from source)
# 3. Create directories with proper permissions
# 4. Copy example config if no config exists at CONFIG_DIR/guardian.yaml
# 5. Install systemd unit: cp systemd/guardian.service $SYSTEMD_DIR/
# 6. systemctl daemon-reload
# 7. Print post-install instructions
```

---

## 15. Intelligence Stub — `guardian/intelligence/stub.py`

```python
# STUB — Phase 2 will implement this fully.
# Do NOT implement ML logic. Just define the interface.

class AnomalyDetector:
    """
    Phase 2: Will implement rolling baseline, z-score detection,
    rate-of-change alerts, and trend forecasting.
    """
    
    def analyze(self, snapshots: dict[str, MetricSnapshot]) -> list[dict]:
        """Returns list of anomaly dicts. Empty list until Phase 2."""
        return []
    
    def update_baseline(self, snapshot: MetricSnapshot) -> None:
        """Update rolling baseline. No-op until Phase 2."""
        pass
```

---

## 16. Utils

### `guardian/utils/logger.py`
```python
# Internal daemon logger (separate from the metric log writer)
# Uses Python stdlib logging
# Format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
# Handler: StreamHandler (stdout) + optionally RotatingFileHandler
# get_logger(name: str) -> logging.Logger
```

### `guardian/utils/retry.py`
```python
# @retry(max_attempts=3, backoff_seconds=1.0, exceptions=(requests.RequestException,))
# Decorator with exponential backoff
# Logs each retry attempt at DEBUG level
```

### `guardian/utils/platform.py`
```python
# is_linux() -> bool
# is_ec2() -> bool  (fast check: does IMDS respond?)
# get_hostname() -> str
# get_boot_time() -> float
# human_bytes(n: int) -> str   ("1.23 GB", "456 MB", etc.)
# human_uptime(seconds: float) -> str   ("2d 14h 32m")
# format_timestamp(ts: float) -> str   (ISO 8601 with TZ)
```

---

## 17. Testing Requirements

Write tests for every module. Use `pytest` + `pytest-mock`.

### Key test patterns:

```python
# Collector tests: mock psutil and subprocess calls
# Example (test_collector_cpu.py):
def test_cpu_collector_returns_snapshot(mocker):
    mocker.patch('psutil.cpu_percent', return_value=45.2)
    mocker.patch('psutil.cpu_percent', side_effect=[[10.0, 20.0, 30.0]])
    # ... assert MetricSnapshot structure

# Alert router tests: test each threshold rule
def test_cpu_critical_threshold_fires_alert():
    config = make_test_config(cpu_critical=95.0)
    router = AlertRouter(config, alerters=[])
    snapshots = make_snapshot('cpu', {'percent_total': 96.0})
    alerts = router.evaluate(snapshots)
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.CRITICAL

# Dedup tests:
def test_alert_dedup_within_cooldown():
    # Same alert twice within cooldown window → only one dispatched

def test_alert_escalation_after_timeout():
    # WARN fires, then 16 minutes pass, then evaluate again → CRITICAL

# Channel tests: mock requests.post
def test_slack_sends_correct_payload(mocker):
    mock_post = mocker.patch('requests.post')
    mock_post.return_value.status_code = 200
    alerter = SlackAlerter(slack_config)
    result = alerter.send(make_test_alert(AlertSeverity.CRITICAL))
    assert result is True
    payload = json.loads(mock_post.call_args[1]['data'])
    assert 'blocks' in payload

# Storage tests: use tmp_path fixture for SQLite
def test_sqlite_insert_and_query(tmp_path):
    store = SQLiteStore(StorageConfig(db_path=str(tmp_path / 'test.db')))
    snapshot = make_snapshot('cpu', {'percent_total': 50.0})
    store.insert_snapshot(snapshot)
    results = store.query_snapshots('cpu', since=0, until=time.time() + 1)
    assert len(results) == 1

# CLI tests: use click.testing.CliRunner + mock API responses
def test_cli_status_command(mocker, cli_runner):
    mocker.patch('requests.get', return_value=mock_status_response())
    result = cli_runner.invoke(cli, ['status'])
    assert result.exit_code == 0
    assert 'Running' in result.output
```

---

## 18. Implementation Order

Follow this exact order. Do not skip ahead.

1. `guardian/config/schema.py` — dataclasses
2. `guardian/config/loader.py` — YAML loading + validation
3. `guardian/utils/` — all utils (logger, retry, platform)
4. `guardian/collector/base.py` — abstract base
5. `guardian/collector/cpu.py`
6. `guardian/collector/memory.py`
7. `guardian/collector/disk.py`
8. `guardian/collector/network.py`
9. `guardian/collector/process.py`
10. `guardian/collector/ec2.py`
11. `guardian/collector/system_events.py`
12. `guardian/collector/app_health.py`
13. `guardian/alerter/base.py`
14. `guardian/alerter/slack.py`
15. `guardian/alerter/telegram.py`
16. `guardian/alerter/email_alerter.py`
17. `guardian/alerter/webhook.py`
18. `guardian/alerter/router.py`
19. `guardian/storage/sqlite_store.py`
20. `guardian/storage/log_writer.py`
21. `guardian/intelligence/stub.py`
22. `guardian/exposition/prometheus.py` (stub)
23. `guardian/exposition/rest_api.py`
24. `guardian/main.py`
25. `guardianctl/cli.py`
26. `config/guardian.example.yaml`
27. `systemd/guardian.service`
28. `scripts/install.sh` + `scripts/uninstall.sh`
29. `pyproject.toml` + `setup.cfg` + `MANIFEST.in`
30. All tests

---

## 19. Critical Implementation Rules

These are non-negotiable:

1. **Never crash the daemon.** Every collector and alerter must catch all exceptions internally and log them. The main loop has a top-level try/except as a final safety net.

2. **Never block the main loop.** All collectors run in a `ThreadPoolExecutor`. All alerter sends run in separate threads. Network calls always have timeouts.

3. **All I/O metrics are rates, not cumulative.** Store previous counter values in instance variables, compute delta per second on each collection.

4. **Config secrets never appear in logs.** The config `show` command and REST API `/config` endpoint must redact: `webhook_url`, `bot_token`, `chat_id`, `smtp_password`, `auth_token`. Replace with `***REDACTED***`.

5. **Python 3.9 compatibility.** Do not use `match/case` (3.10+), `X | Y` union types in annotations (use `Optional[X]`), or `dict[str, int]` in non-string annotations (use `Dict[str, int]` from typing or `from __future__ import annotations`).

6. **No heavy dependencies.** Only what's in `pyproject.toml`. No Flask, FastAPI, SQLAlchemy, Celery, Redis, etc. The REST API uses stdlib `http.server`.

7. **The daemon must survive network outages.** If all alerters fail to send, log locally and retry on next relevant cycle. Never mark an alert as "sent" unless at least one channel confirmed delivery.

8. **Heartbeat file must be updated every collection cycle.** Path: `/var/run/guardian/guardian.heartbeat`. Format: plain unix timestamp (float as string). This enables external watchdog scripts to detect a hung daemon.

9. **PID file must be written on start and removed on clean stop.** Path: `/var/run/guardian/guardian.pid`.

10. **`guardianctl` must degrade gracefully if the daemon is not running.** It should show a clear error message and exit code 1, not a raw connection error traceback.

---

## 20. README.md Outline

Write a complete README with:
- What it is (2 paragraphs)
- Architecture diagram (ASCII)
- Prerequisites (Python 3.9+, Linux, systemd)
- Quick Install (one-liner + manual)
- Configuration guide (key sections explained)
- `guardianctl` command reference (all commands with examples)
- Alert channel setup guides (Slack webhook, Telegram bot, Gmail app password)
- Extending: how to add a new collector (implement BaseCollector)
- How to connect Grafana/Prometheus (Phase 3 placeholder)
- License

---

*End of CLAUDE.md*
