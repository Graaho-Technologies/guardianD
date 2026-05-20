# GuardianD

Production-grade EC2 instance observability daemon. Runs as a systemd service on Linux or standalone on macOS. Collects system metrics every 10 seconds, detects anomalies, fires alerts to Telegram/Slack/Email, and serves a Prometheus `/metrics` endpoint for Grafana dashboards — no Datadog, no CloudWatch agent, no Node Exporter required.

## What It Does

| Layer | What you get |
|---|---|
| **Collectors** | CPU, memory, disk, network, processes, EC2 metadata, system events, app health |
| **Intelligence** | Anomaly detection (z-score), velocity spikes, trend forecasting, bottleneck fingerprinting |
| **Alerts** | Threshold + anomaly alerts → Telegram, Slack, Email, Webhook. Cooldown, escalation, recovery |
| **Prometheus** | `/metrics` endpoint on `:9732`, 67+ metrics, Grafana dashboard included |
| **CLI** | `guardianctl` — live status, metrics, alerts, logs, config, test alerts |

## Architecture

```
Collectors (threaded, every 10s)
  cpu · memory · disk · network · process · ec2 · system_events · app_health
        │
        ▼
Intelligence Layer (numpy, optional)
  BaselineEngine → AnomalyDetector → VelocityDetector → TrendForecaster → BottleneckFingerprinter
        │
        ▼
AlertRouter (threshold eval + dedup + cooldown + escalation + recovery)
        │
        ├─→ Telegram · Slack · Email · Webhook
        ├─→ SQLite (metric_snapshots, alerts, baselines)
        ├─→ JSONL rotating logs
        ├─→ Prometheus :9732/metrics ←── Prometheus Server ←── Grafana
        └─→ REST API :9731 ←── guardianctl CLI
```

---

## Prerequisites

- Python 3.9+
- Linux (systemd, full feature set) or macOS (dev/testing)
- Root access for production install (PID file, heartbeat)
- `numpy` and `prometheus-client` for intelligence + Grafana (included in `full` extras)

---

## Installation

### 1. Clone and install

```bash
git clone https://github.com/Graaho-Technologies/guardianD.git
cd guardianD

# Full install (includes numpy + prometheus-client)
pip install -e ".[full]"
```

Verify install:

```bash
guardiand --version
guardianctl --help
```

### 2. Generate config

```bash
guardianctl init --output ~/guardian/guardian.yaml
```

This creates a fully-commented config and prints next steps:

```
Config written to ~/guardian/guardian.yaml

Next steps:
  1. Set instance name:  edit ~/guardian/guardian.yaml  →  instance_name: my-server
  2. Enable Telegram:    guardianctl setup telegram --config ~/guardian/guardian.yaml
  3. Validate config:    guardianctl --config ~/guardian/guardian.yaml config validate
  4. Start daemon:       guardiand --config ~/guardian/guardian.yaml
  5. Check status:       guardianctl --config ~/guardian/guardian.yaml status

Note: at least one alert channel must be enabled before the daemon will accept the config.
```

### 3. Set your instance name

Open `~/guardian/guardian.yaml` and set:

```yaml
instance_name: my-server     # appears in every alert and Grafana
environment: production
```

Also set storage paths (defaults work for root on Linux):

```yaml
storage:
  log_dir: /var/log/guardian          # or ~/guardian/logs on macOS
  db_path: /var/lib/guardian/metrics.db  # or ~/guardian/data/metrics.db on macOS
```

---

## Alert Channel Setup

### Telegram (recommended — easiest)

Run the interactive wizard:

```bash
guardianctl setup telegram --config ~/guardian/guardian.yaml
```

The wizard will:
1. Ask for your bot token (create one at [@BotFather](https://t.me/BotFather))
2. Auto-detect your chat ID from recent messages
3. Send a test message to confirm delivery
4. Write the credentials into your config

Or set manually in `guardian.yaml`:

```yaml
alerts:
  telegram:
    enabled: true
    bot_token: "7123456789:AAF..."
    chat_id: "123456789"
    min_severity: WARN    # INFO | WARN | CRITICAL | EMERGENCY
```

### Slack

1. Create an Incoming Webhook at `api.slack.com/apps`
2. Add to config:

```yaml
alerts:
  slack:
    enabled: true
    webhook_url: "https://hooks.slack.com/services/..."
    channel: "#alerts"
    min_severity: WARN
```

Or export: `export GUARDIAN_SLACK_WEBHOOK=https://hooks.slack.com/...`

### Email (Gmail)

1. Enable 2FA, create App Password at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Add to config:

```yaml
alerts:
  email:
    enabled: true
    smtp_host: smtp.gmail.com
    smtp_port: 587
    smtp_user: you@gmail.com
    smtp_password: "your-app-password"   # or GUARDIAN_EMAIL_PASSWORD env var
    from_addr: you@gmail.com
    to_addrs:
      - oncall@yourcompany.com
    min_severity: CRITICAL
```

### Webhook (PagerDuty / OpsGenie compatible)

```yaml
alerts:
  webhook:
    enabled: true
    url: "https://events.pagerduty.com/v2/enqueue"
    secret: "optional-hmac-secret"
    min_severity: CRITICAL
```

---

## Validate and Start

```bash
# Validate config (catches missing fields, wrong thresholds, port conflicts)
guardianctl --config ~/guardian/guardian.yaml config validate

# Start daemon (foreground, logs to terminal)
guardiand --config ~/guardian/guardian.yaml

# Or background
guardiand --config ~/guardian/guardian.yaml >> ~/guardian/logs/daemon.log 2>&1 &

# Check it's running
guardianctl --config ~/guardian/guardian.yaml status
```

---

## Production Install (Linux + systemd)

```bash
sudo bash scripts/install.sh --full
```

The install script:
- Installs guardianD with full extras via pip
- Creates `/etc/guardian/`, `/var/log/guardian/`, `/var/lib/guardian/`
- Copies `systemd/guardian.service`
- Generates example config at `/etc/guardian/guardian.yaml`

Then:

```bash
sudo nano /etc/guardian/guardian.yaml
sudo systemctl start guardian
sudo systemctl enable guardian
guardianctl status
```

Hot reload config without restart:

```bash
guardianctl config reload   # sends SIGHUP to daemon
```

---

## Grafana Dashboard

GuardianD ships with a complete importable Grafana dashboard (`grafana/dashboard.json`) covering all 67 metrics across 9 sections.

### Quick setup

**Step 1 — Install Prometheus**

```bash
# macOS
brew install prometheus

# Linux
# Download from https://prometheus.io/download/
```

**Step 2 — Configure Prometheus to scrape GuardianD**

Add to `prometheus.yml`:

```yaml
global:
  scrape_interval: 10s

scrape_configs:
  - job_name: "guardiand"
    static_configs:
      - targets: ["localhost:9732"]
```

Start Prometheus:

```bash
# macOS
brew services start prometheus

# Linux
prometheus --config.file=/etc/prometheus/prometheus.yml
```

**Step 3 — Install and start Grafana**

```bash
# macOS
brew install grafana
brew services start grafana
```

Grafana runs at `http://localhost:3000` (default login: `admin` / `admin`).

**Step 4 — Add Prometheus datasource**

In Grafana: **Connections → Data sources → Add new data source → Prometheus**

(Older Grafana: **Configuration → Data Sources → Add data source → Prometheus**)

Set URL: `http://localhost:9090` ← Prometheus server port, NOT `:9732`

Click **Save & Test** — should show "Successfully queried the Prometheus API."

Or via API:

```bash
curl -X POST http://admin:admin@localhost:3000/api/datasources \
  -H "Content-Type: application/json" \
  -d '{"name":"GuardianD","type":"prometheus","url":"http://localhost:9090","isDefault":true}'
```

**Step 5 — Import the dashboard**

In Grafana: **Dashboards → Import → Upload JSON file**

Select: `grafana/dashboard.json`

Select your Prometheus datasource → **Import**

Or use the setup wizard for guided instructions:

```bash
guardianctl setup grafana
```

**Step 6 — View data**

Open `http://localhost:3000/d/guardiand-ec2-v1`

Select your instance from the **Instance** dropdown at the top.

### Dashboard sections

| Row | Panels |
|---|---|
| Quick Overview | CPU%, Load, RAM, Swap, Disk, CPU Steal, PSI pressure stats |
| Basic | CPU time breakdown, Memory, Network throughput, Disk space |
| CPU Detail | Steal%, iowait%, context switches, load avg, per-core heatmap |
| Memory | Dirty pages, FD usage, HugePages, OOM kills, swap rates |
| Disk I/O | Read/write bytes, IOPS, latency by disk type, utilization |
| Network | Per-interface throughput, error/drop rates, TCP states, DNS |
| EC2 | Instance info, spot interruption status, CPU steal trend |
| App Health | Health check status table + latency |
| Alerts | Fired/recovered counters, active count, anomaly scores |

---

## Prometheus Metrics Reference

Enable in config:

```yaml
prometheus:
  enabled: true
  host: "0.0.0.0"   # or 127.0.0.1 for local-only
  port: 9732
  path: /metrics
```

Metrics are available at `http://localhost:9732/metrics` in Prometheus text format.

Key metrics:

```
guardian_cpu_usage_percent          # overall CPU %
guardian_cpu_steal_percent          # EC2 hypervisor steal %
guardian_cpu_iowait_percent         # I/O wait %
guardian_memory_usage_ratio         # 0.0–1.0
guardian_swap_usage_ratio           # 0.0–1.0
guardian_swap_out_pages_per_second  # active swap pressure
guardian_disk_usage_ratio           # per mountpoint
guardian_disk_await_milliseconds    # per disk, by type
guardian_tcp_connections            # per state (established, close_wait, etc.)
guardian_dns_latency_milliseconds
guardian_alerts_fired_total         # counter, by severity + category
guardian_psi_*_stall_ratio          # PSI pressure (Linux 4.20+ only)
guardian_ec2_spot_interruption_scheduled  # 1 = termination notice received
```

All metrics carry labels: `instance_id`, `instance_name`, `environment`.

---

## Intelligence Layer

Enabled by default when `numpy` is installed. Runs after a warm-up period (default 2 minutes, configurable).

```yaml
intelligence:
  enabled: true
  baseline_window_hours: 24    # rolling window for baseline
  baseline_min_samples: 30     # minimum samples before anomaly detection fires
  warmup_minutes: 5            # suppress intelligence alerts for 5 min after start
  velocity_enabled: true       # rate-of-change alerts
  forecast_enabled: true       # linear regression trend forecasting
```

What it detects:

| Module | What |
|---|---|
| **AnomalyDetector** | Z-score spikes vs rolling 24h baseline (fires when z > 2.0 warn / 3.0 critical) |
| **VelocityDetector** | Sudden % increases in CPU, memory, IOPS, TCP connections |
| **TrendForecaster** | Linear regression on disk/memory — alerts "Disk full in 3.2h" |
| **BottleneckFingerprinter** | Correlates patterns: "CPU-bound", "I/O bottleneck", "EC2 noisy neighbor", "Connection leak" |

Intelligence alerts appear in Telegram/Slack the same as threshold alerts, enriched with root cause diagnosis.

---

## guardianctl Command Reference

```bash
# Status and monitoring
guardianctl status                          # daemon health, collectors, active alerts
guardianctl metrics                         # all collector snapshots
guardianctl metrics --collector cpu         # single collector
guardianctl metrics --watch --interval 5    # live refresh
guardianctl top                             # htop-style live view with PSI indicators

# Alerts
guardianctl alerts                          # recent alert history
guardianctl alerts --active                 # currently firing
guardianctl alerts --severity CRITICAL --since 2h
guardianctl test-alert --channel telegram --severity WARN

# Intelligence
guardianctl anomalies                       # recent anomaly detections
guardianctl anomalies --min-score 2.5
guardianctl baseline --collector memory     # baseline stats per metric
guardianctl forecast                        # active trend forecasts
guardianctl diagnose                        # run bottleneck fingerprinter now

# Config
guardianctl config show                     # print config (secrets redacted)
guardianctl config validate                 # validate config file
guardianctl config reload                   # hot reload (SIGHUP)
guardianctl init --output ./guardian.yaml   # generate example config

# Setup wizards
guardianctl setup telegram                  # interactive Telegram setup
guardianctl setup grafana                   # Grafana/Prometheus setup guide

# Prometheus
guardianctl prometheus status               # show if enabled, scrape URL
guardianctl prometheus url                  # print base URL

# Logs
guardianctl logs                            # recent log entries
guardianctl logs --follow                   # tail -f
guardianctl logs --lines 50 --level CRITICAL

# App health checks
guardianctl health                          # all configured health checks

# Daemon lifecycle (Linux systemd)
guardianctl start
guardianctl stop
guardianctl restart
guardianctl install --systemd              # install as systemd service
guardianctl uninstall
```

All commands accept `--config PATH` to point at a non-default config file.

---

## App Health Checks

Monitor your own services:

```yaml
app_health_checks:
  - name: my-api
    type: http
    target: http://localhost:8080/health
    expected_status_code: 200
    timeout_seconds: 5
    critical_on_failure: true

  - name: postgres
    type: port
    target: localhost:5432
    timeout_seconds: 3

  - name: nginx
    type: process
    target: nginx

  - name: my-worker
    type: systemd_service
    target: my-worker.service
```

Types: `http`, `port`, `process`, `systemd_service`

---

## Custom Alert Thresholds

All thresholds are configurable in `guardian.yaml`:

```yaml
thresholds:
  # CPU
  cpu_warn: 80.0
  cpu_critical: 95.0
  cpu_steal_warn: 5.0          # EC2 only — hypervisor steal
  cpu_iowait_warn: 40.0

  # Memory
  memory_warn: 80.0
  memory_critical: 92.0
  swap_warn: 50.0
  swap_critical: 80.0

  # Disk
  disk_warn: 85.0
  disk_critical: 95.0

  # Disk I/O latency — per disk type
  disk_await_ssd_warn_ms: 10.0
  disk_await_ebs_warn_ms: 20.0
  disk_await_hdd_warn_ms: 100.0

  # Network
  network_error_rate_warn: 0.1   # %
  tcp_close_wait_warn: 100       # connection leak indicator

  # Intelligence
  anomaly_zscore_warn: 2.0
  anomaly_zscore_critical: 3.0
  forecast_disk_full_warn_hours: 8.0
  forecast_disk_full_critical_hours: 2.0
```

---

## Troubleshooting

**Permission denied on `/var/run/guardian`**
Normal on non-root. Heartbeat and PID file need root. Run with `sudo` in production or accept the warnings in dev.

**EC2 collector timeout (2s per cycle)**
Expected on non-EC2. Default is `ec2_imds_timeout: 2`. Lower to `1` to reduce startup time on non-EC2 hosts. On actual EC2, IMDS responds in <5ms.

**PSI metrics show 0 on macOS**
Expected — PSI requires Linux kernel 4.20+. Panels show 0, not "No data".

**`intelligence: warming up` in status**
Wait for `warmup_minutes` (default 2). After warm-up, anomaly/velocity/forecast detectors activate.

**Grafana shows "No data"**
1. Check Prometheus is scraping: `curl http://localhost:9090/api/v1/query?query=guardian_cpu_usage_percent`
2. Check datasource URL is `http://localhost:9090` (Prometheus port), NOT `:9732`
3. Select your instance from the **Instance** dropdown in the dashboard

**Config validation fails**
At least one alert channel must be enabled. Run `guardianctl setup telegram` to configure one quickly.

---

## Adding a Custom Collector

```python
# guardian/collector/my_collector.py
from .base import BaseCollector, MetricSnapshot
import time

class MyCollector(BaseCollector):
    name = "my_collector"

    def collect(self) -> MetricSnapshot:
        try:
            metrics = {"my_metric": 42.0}
            return MetricSnapshot(
                collector_name=self.name,
                timestamp=time.time(),
                metrics=metrics,
            )
        except Exception as exc:
            return self._error_snapshot(str(exc))
```

Register in `GuardianDaemon._init_collectors()` in `guardian/main.py`. Add threshold rules in `AlertRouter.evaluate()` in `guardian/alerter/router.py`.

---

## Development

```bash
# Install dev extras
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=guardian --cov-report=term-missing

# Lint
ruff check guardian/
```

---

## License

MIT — see LICENSE file.
