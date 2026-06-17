# GuardianD

A lightweight observability daemon for a single Linux/macOS/Windows machine (built for EC2, works anywhere). It watches CPU, memory, disk, network, processes, and your own services, then alerts you on Telegram / Slack / Email when something breaks — no Datadog, no CloudWatch agent, no Node Exporter.

If you only read one thing, read **[The 4 moving parts](#the-4-moving-parts)** below. Most confusion comes from not knowing which pieces are required and which are optional.

---

## The 4 moving parts

GuardianD is **one program** (`guardiand`). The other three names you keep seeing are separate tools that plug into it. Here is the whole picture:

```
   THE MACHINE YOU WANT TO MONITOR
   ┌─────────────────────────────────────────────────┐
   │  guardiand   ◄── THE ENGINE. The only required    │
   │                  piece. Runs forever.             │
   │   • collects CPU/mem/disk/net every 10s           │
   │   • fires alerts → Telegram / Slack / Email       │
   │   • stores metrics in SQLite + log files          │
   │   • opens TWO local HTTP ports:                   │
   │        :9731  REST API   ──►  guardianctl         │
   │        :9732  /metrics   ──►  prometheus          │
   └──────────┬────────────────────────┬───────────────┘
              │ :9731                   │ :9732
              ▼                         ▼
        guardianctl              prometheus   (separate download)
        CLI remote control       stores metric history
        status / alerts / logs          │
                                         ▼
                                   grafana   (separate download)
                                   the pretty dashboards
```

| Part | What it is | Required? | Where it runs |
|------|-----------|-----------|---------------|
| **guardiand** | The daemon/engine. Collects metrics, sends alerts, stores data. | **YES** | On the machine you monitor |
| **guardianctl** | A command-line remote control. It just *talks to* `guardiand` over the REST API (`:9731`). It collects nothing itself. | No — convenience | Usually same machine |
| **prometheus** | A separate, third-party time-series database. It *pulls* metrics from `guardiand:9732` every few seconds and keeps history. | No — only for dashboards/history | Anywhere |
| **grafana** | A separate, third-party dashboard UI. It reads from **prometheus** (never from guardiand directly) and draws the graphs. | No — only for dashboards | Anywhere |

**The one thing to remember:** prometheus and grafana are **not part of GuardianD**. GuardianD only *publishes* a metrics page at `:9732/metrics`. Prometheus pulls that page; Grafana reads prometheus. The data flows in one direction:

```
guardiand ──► prometheus ──► grafana          (dashboards, optional)
guardiand ──► guardianctl                      (terminal control, optional)
```

### So what do I actually need?

| You want… | Install |
|-----------|---------|
| **Just alerts** (Telegram/Slack/Email) when the box is unhealthy | `guardiand` + one alert channel. **Stop there.** |
| Alerts **+ a terminal dashboard** (`guardianctl top`, `status`, `alerts`) | add nothing — `guardianctl` ships with it |
| Alerts **+ web dashboards / long-term graphs** | also install prometheus + grafana ([guide below](#optional-dashboards-prometheus--grafana)) |

Beginners: do the **[Minimal setup](#minimal-setup-5-minutes-any-os)** first. Add prometheus/grafana later only if you miss the graphs.

---

## Minimal setup (5 minutes, any OS)

This gets you `guardiand` running with Telegram alerts. No root, no systemd, no prometheus.

### Step 0 — Prerequisites

- **Python 3.9+** (`python3 --version`)
- Linux, macOS, or Windows (see [platform notes](#platform-support))

### Step 1 — Get the code and install into a virtual environment

> **Why a venv?** Modern Python (Debian/Ubuntu/Homebrew) refuses `pip install` into the system Python with an `externally-managed-environment` error. A venv sidesteps that and keeps things clean. Do this on every OS.

**Linux / macOS:**

```bash
git clone https://github.com/Graaho-Technologies/guardianD.git
cd guardianD

python3 -m venv .venv
source .venv/bin/activate

pip install -e .          # core (alerts only)
# pip install -e ".[full]"  # core + intelligence (numpy) + prometheus support
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/Graaho-Technologies/guardianD.git
cd guardianD

py -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e .
```

Verify:

```bash
guardiand --version       # -> GuardianD 0.1.0
guardianctl --help
```

> The `guardiand` / `guardianctl` commands only exist while the venv is **activated**. Open a new terminal? Re-run `source .venv/bin/activate` (or the Windows equivalent) first.

### Step 2 — Generate a config

```bash
guardianctl init --output ~/guardian/guardian.yaml
```

This writes a fully-commented `guardian.yaml` (parent folders are created for you).

### Step 3 — Set the basics

Open `~/guardian/guardian.yaml` and edit two things.

**1. Name this machine:**

```yaml
instance_name: my-server        # shows up in every alert
environment: production          # production | staging | dev
```

**2. Storage paths — IMPORTANT if you are not root.** The defaults point at `/var/log/guardian` and `/var/lib/guardian`, which only root can write. Running as a normal user (typical on macOS/Windows/dev), point them at your home folder instead:

```yaml
storage:
  log_dir: ~/guardian/logs
  db_path: ~/guardian/data/metrics.db
```

> Non-root note: you will still see harmless `Permission denied: /var/run/guardian` warnings for the PID/heartbeat files. They don't stop the daemon. They disappear when you run as root / via systemd.

### Step 4 — Turn on one alert channel

Telegram is the quickest. Run the wizard:

```bash
guardianctl --config ~/guardian/guardian.yaml setup telegram
```

It asks for a bot token (make one with [@BotFather](https://t.me/BotFather)), auto-detects your chat ID, sends a test message, and writes both into your config.

Prefer to edit by hand? See **[Alert channels](#alert-channels)**. At least **one** channel must be enabled or the config won't validate.

### Step 5 — Validate, start, check

```bash
# Validate (catches typos, missing fields, no-channel-enabled)
guardianctl --config ~/guardian/guardian.yaml config validate

# Start in the foreground (Ctrl-C to stop) — best for first run
guardiand --config ~/guardian/guardian.yaml

# …or in the background
guardiand --config ~/guardian/guardian.yaml >> ~/guardian/logs/daemon.log 2>&1 &

# In another terminal (venv activated), check it
guardianctl --config ~/guardian/guardian.yaml status
```

`status` should show `● Running`, all collectors `ok`, and your instance name. **That's a complete, useful install.** Stop here unless you want web dashboards.

> Tip: set `export GUARDIAN_CONFIG=~/guardian/guardian.yaml` once and you can drop the `--config` flag from every command.

---

## Production install (Linux + systemd)

This makes `guardiand` a real system service: starts on boot, restarts on crash, runs as root (so PID/heartbeat files and `dmesg` work). **Linux only** — macOS and Windows use the manual start from Step 5 above.

> **Fast path:** from a cloned repo, `sudo bash scripts/install.sh --full` does steps 1–3 for you — installs from the clone, creates the directories, generates `/etc/guardian/guardian.yaml`, and installs + enables the systemd unit. Edit the config, then `sudo systemctl start guardian`. The manual steps below are the equivalent if you'd rather run them yourself.

### 1. Install the package system-wide

```bash
git clone https://github.com/Graaho-Technologies/guardianD.git
cd guardianD
sudo pip install ".[full]"        # installs guardiand + guardianctl for all users
which guardiand                   # note this path — you need it next
```

### 2. Create directories and config

```bash
sudo mkdir -p /etc/guardian /var/log/guardian /var/lib/guardian
sudo guardianctl init --output /etc/guardian/guardian.yaml
sudo nano /etc/guardian/guardian.yaml      # set instance_name + enable a channel
```

As root, the default `/var/log/guardian` and `/var/lib/guardian` paths work — leave them.

### 3. Install the systemd unit

The shipped `systemd/guardian.service` assumes `guardiand` lives at `/usr/local/bin/guardiand`. Confirm with the `which guardiand` from step 1; if it differs, fix the `ExecStart=` line.

```bash
sudo cp systemd/guardian.service /etc/systemd/system/

# If `which guardiand` was NOT /usr/local/bin/guardiand, point ExecStart at the real path:
# sudo sed -i "s#/usr/local/bin/guardiand#$(which guardiand)#" /etc/systemd/system/guardian.service

sudo systemctl daemon-reload
sudo systemctl enable --now guardian        # enable on boot + start now
sudo systemctl status guardian
guardianctl --config /etc/guardian/guardian.yaml status
```

### Day-2 operations

```bash
sudo systemctl restart guardian             # restart
sudo systemctl stop guardian                # stop
guardianctl config reload                   # hot-reload config (sends SIGHUP, no restart)
journalctl -u guardian -f                   # follow service logs
```

---

## Platform support

GuardianD never crashes on a missing feature — unsupported collectors quietly report nothing. What you actually get:

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | Full | systemd service, `dmesg` kernel events, PSI pressure stalls (kernel 4.20+), OOM-kill detection, EC2 IMDS. |
| **macOS** | Good for dev/personal | Core CPU/mem/disk/net/process metrics + alerts work. No systemd, no PSI, no `dmesg`. Run manually (Step 5). |
| **Windows** | Basic, untested | Core psutil metrics + alerts work. No systemd, `dmesg`, PSI, EC2 IMDS, or `systemd_service` health checks. Run `guardiand --config ...` in a terminal, or wrap it as a service with [NSSM](https://nssm.cc/) / Task Scheduler. |

EC2-specific metrics (instance ID, spot-interruption notice, CPU steal) only populate on an actual EC2 instance. Elsewhere the EC2 collector times out once (~2s) and marks `is_ec2: false`.

---

## Alert channels

At least one must be `enabled: true`. Secrets can come from the config file **or** environment variables (env wins).

### Telegram (easiest)

```bash
guardianctl --config ~/guardian/guardian.yaml setup telegram
```

…or by hand:

```yaml
alerts:
  telegram:
    enabled: true
    bot_token: "7123456789:AAF..."   # or env GUARDIAN_TELEGRAM_TOKEN
    chat_id: "123456789"             # or env GUARDIAN_TELEGRAM_CHAT_ID
    min_severity: WARN               # INFO | WARN | CRITICAL | EMERGENCY
```

### Slack

Create an Incoming Webhook at `api.slack.com/apps`, then:

```yaml
alerts:
  slack:
    enabled: true
    webhook_url: "https://hooks.slack.com/services/..."   # or env GUARDIAN_SLACK_WEBHOOK
    channel: "#alerts"
    min_severity: WARN
```

### Email (Gmail)

Enable 2FA, create an App Password at [myaccount.google.com/security](https://myaccount.google.com/security), then:

```yaml
alerts:
  email:
    enabled: true
    smtp_host: smtp.gmail.com
    smtp_port: 587
    smtp_user: you@gmail.com
    smtp_password: "your-app-password"   # or env GUARDIAN_EMAIL_PASSWORD
    from_addr: you@gmail.com
    to_addrs:
      - oncall@yourcompany.com
    min_severity: CRITICAL
```

### Webhook (PagerDuty / OpsGenie / custom)

```yaml
alerts:
  webhook:
    enabled: true
    url: "https://events.pagerduty.com/v2/enqueue"
    secret: "optional-hmac-secret"
    min_severity: CRITICAL
```

Send a test to confirm delivery:

```bash
guardianctl test-alert --channel telegram --severity WARN
guardianctl test-alert --channel all --severity CRITICAL
```

---

## Optional: dashboards (Prometheus + Grafana)

Skip this entirely unless you want web graphs and metric history. Reminder: these are **two separate programs** you download yourself; GuardianD just feeds them.

### Step 1 — Turn on GuardianD's metrics endpoint

It is **off by default**. Edit your `guardian.yaml`:

```yaml
prometheus:
  enabled: true        # ← must flip this to true
  host: "0.0.0.0"      # or 127.0.0.1 for local-only
  port: 9732
  path: /metrics
```

Reload (`guardianctl config reload`) or restart, then confirm:

```bash
curl http://localhost:9732/metrics | head
```

You should see lines like `guardian_cpu_usage_percent{...} 22.0`. If this is empty, prometheus/grafana cannot work — fix it here first.

### Step 2 — Install Prometheus and point it at GuardianD

```bash
# macOS
brew install prometheus
# Linux: download from https://prometheus.io/download/
```

Add to `prometheus.yml`:

```yaml
global:
  scrape_interval: 10s

scrape_configs:
  - job_name: "guardiand"
    static_configs:
      - targets: ["localhost:9732"]   # GuardianD's metrics port
```

Start it (`brew services start prometheus`, or `prometheus --config.file=...`). Prometheus itself runs on **`:9090`**.

### Step 3 — Install Grafana and add Prometheus as a data source

```bash
# macOS
brew install grafana && brew services start grafana
```

Open `http://localhost:3000` (login `admin` / `admin`). Then **Connections → Data sources → Add → Prometheus** and set:

```
URL:  http://localhost:9090      ← Prometheus port, NOT 9732
```

Click **Save & Test**.

### Step 4 — Import the dashboard

**Dashboards → Import → Upload JSON file** → pick `grafana/dashboard.json` (ships in this repo, covers all 67 metrics) → select your Prometheus data source → **Import**. Open it, then pick your host from the **Instance** dropdown.

Guided walkthrough: `guardianctl setup grafana`.

### The ports, untangled

| Port | Belongs to | Who connects to it |
|------|-----------|--------------------|
| `9731` | GuardianD REST API | `guardianctl` |
| `9732` | GuardianD `/metrics` | Prometheus scrapes it |
| `9090` | Prometheus | Grafana reads from it |
| `3000` | Grafana | your browser |

Most "No data" problems are putting `9732` where Grafana expects `9090`.

---

## guardianctl command reference

All commands accept `--config PATH` (or set `GUARDIAN_CONFIG`). They talk to a **running** daemon over the REST API — if the daemon is down you get a clear error, not a crash.

```bash
# Status & live monitoring
guardianctl status                          # daemon health, collectors, active alerts
guardianctl metrics                         # all collector snapshots
guardianctl metrics --collector cpu         # one collector
guardianctl metrics --watch --interval 5    # live refresh
guardianctl top                             # htop-style live view

# Alerts
guardianctl alerts                          # recent history
guardianctl alerts --active                 # currently firing
guardianctl alerts --severity CRITICAL --since 2h
guardianctl test-alert --channel telegram --severity WARN

# Intelligence (needs the [full]/[intelligence] extra)
guardianctl anomalies                       # recent anomaly detections
guardianctl baseline --collector memory     # baseline stats
guardianctl forecast                        # disk/memory fill predictions
guardianctl diagnose                        # run bottleneck fingerprinter now

# Config
guardianctl config show                     # print config (secrets redacted)
guardianctl config validate                 # validate the file
guardianctl config reload                   # hot reload (SIGHUP)
guardianctl init --output ./guardian.yaml   # generate example config

# Setup wizards
guardianctl setup telegram                  # interactive Telegram setup
guardianctl setup grafana                   # Grafana/Prometheus guide

# Prometheus helpers
guardianctl prometheus status               # enabled? scrape URL?
guardianctl prometheus url

# Logs & health
guardianctl logs --follow                   # tail -f
guardianctl logs --lines 50 --level CRITICAL
guardianctl health                          # app health-check results
```

> `guardiand` (the daemon) supports `--version`. `guardianctl` does not — use `guardianctl --help`.

---

## App health checks

Have GuardianD watch your own services and alert when one goes down:

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

  - name: my-worker          # Linux only
    type: systemd_service
    target: my-worker.service
```

Types: `http`, `port`, `process`, `systemd_service` (Linux only).

---

## Custom alert thresholds

Everything is tunable in `guardian.yaml`:

```yaml
thresholds:
  cpu_warn: 80.0
  cpu_critical: 95.0
  cpu_steal_warn: 5.0          # EC2 only — hypervisor steal
  memory_warn: 80.0
  memory_critical: 92.0
  swap_warn: 50.0
  swap_critical: 80.0
  disk_warn: 85.0
  disk_critical: 95.0
  disk_await_ssd_warn_ms: 10.0
  disk_await_ebs_warn_ms: 20.0
  network_error_rate_warn: 0.1   # %
  tcp_close_wait_warn: 100       # connection-leak indicator
  # Intelligence (needs numpy)
  anomaly_zscore_warn: 2.0
  anomaly_zscore_critical: 3.0
  forecast_disk_full_warn_hours: 8.0
```

Rule of thumb: every `*_warn` must be **less than** its matching `*_critical`, or validation fails.

---

## Intelligence layer (optional, needs numpy)

Active by default when `numpy` is installed (`pip install -e ".[full]"`). It learns a rolling baseline, then flags statistical anomalies, sudden spikes, and trends ("disk full in 3.2h"). It warms up first (default ~2 min) — `status` shows `intelligence: warming up` until then.

```yaml
intelligence:
  enabled: true
  baseline_window_hours: 24
  baseline_min_samples: 30
  warmup_minutes: 5
  velocity_enabled: true
  forecast_enabled: true
```

Anomaly alerts arrive on the same channels as threshold alerts, with a root-cause note attached.

---

## Troubleshooting

**`error: externally-managed-environment` during pip install**
You're installing into the system Python. Use a venv (Step 1) — that's the fix.

**`guardiand: command not found`**
The venv isn't activated. Run `source .venv/bin/activate` (Linux/macOS) or `.\.venv\Scripts\Activate.ps1` (Windows) in this terminal.

**`Permission denied: /var/run/guardian`**
Normal when not root. PID/heartbeat files need root. Harmless in dev; gone under systemd/root.

**`config validation failed: At least one alert channel must be enabled`**
Enable Telegram/Slack/Email/Webhook in `guardian.yaml`. Quickest: `guardianctl setup telegram`.

**`Permission denied` writing logs or the database**
You set `log_dir`/`db_path` under `/var/...` but aren't root. Point them at `~/guardian/...` (Step 3).

**EC2 collector takes ~2s per cycle on a non-EC2 box**
Expected — it's waiting on IMDS that isn't there. Lower `collector.ec2_imds_timeout` to `1`.

**PSI / `dmesg` metrics show 0 on macOS/Windows**
Expected — those are Linux-only. Panels show 0, not an error.

**Grafana shows "No data"**
1. Is GuardianD's endpoint on? `curl http://localhost:9732/metrics` (set `prometheus.enabled: true`).
2. Is Prometheus scraping? `curl 'http://localhost:9090/api/v1/query?query=guardian_cpu_usage_percent'`
3. Is the Grafana data source URL `http://localhost:9090` (Prometheus), **not** `:9732`?
4. Pick your host from the **Instance** dropdown.

**`scripts/install.sh` fails**
Known issue — it installs from PyPI, where this package isn't published. Use the [manual production steps](#production-install-linux--systemd) instead.

---

## Adding a custom collector

```python
# guardian/collector/my_collector.py
from .base import BaseCollector, MetricSnapshot
import time

class MyCollector(BaseCollector):
    name = "my_collector"

    def collect(self) -> MetricSnapshot:
        try:
            return MetricSnapshot(
                collector_name=self.name,
                timestamp=time.time(),
                metrics={"my_metric": 42.0},
            )
        except Exception as exc:
            return self._error_snapshot(str(exc))
```

Register it in `GuardianDaemon._init_collectors()` (`guardian/main.py`), and add threshold rules in `AlertRouter.evaluate()` (`guardian/alerter/router.py`).

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
pytest tests/ --cov=guardian --cov-report=term-missing
ruff check guardian/
```

---

## License

MIT — see [LICENSE](LICENSE).
