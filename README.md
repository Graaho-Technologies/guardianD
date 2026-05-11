# GuardianD

GuardianD is a production-grade EC2 instance observability daemon written in Python. It runs as a systemd service, continuously collecting system and AWS-specific metrics, detecting anomalies and threshold breaches, and routing alerts to Slack, Telegram, and Email. It requires no external time-series database or agent sidecar — all state is persisted locally in SQLite and rotating JSON log files.

GuardianD ships with `guardianctl`, a rich CLI for operators to inspect daemon status, view live metrics, query alert history, manage configuration, and fire test alerts — all backed by a lightweight REST API served by the daemon itself.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        GuardianD Daemon                         │
│                                                                 │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │  Collectors │   │ Alert Router │   │    REST API :9731    │ │
│  │  (threaded) │──▶│  + Dedup     │──▶│   (guardianctl)      │ │
│  │             │   │  + Cooldown  │   └──────────────────────┘ │
│  │  cpu        │   │  + Escalation│                            │
│  │  memory     │   └──────┬───────┘   ┌──────────────────────┐ │
│  │  disk       │          │           │      Storage          │ │
│  │  network    │          ▼           │  SQLite + JSONL logs  │ │
│  │  process    │   ┌──────────────┐   └──────────────────────┘ │
│  │  ec2 (IMDS) │   │   Alerters   │                            │
│  │  sys_events │   │  Slack       │                            │
│  │  app_health │   │  Telegram    │                            │
│  └─────────────┘   │  Email       │                            │
│                    │  Webhook     │                            │
│                    └──────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Python 3.9+
- Linux (systemd-based)
- Root access for systemd installation

## Quick Install

```bash
# From source
git clone https://github.com/Graaho-Technologies/guardianD.git
cd guardianD
bash scripts/install.sh
```

Manual:

```bash
pip install guardiand
guardianctl install
# edit /etc/guardian/guardian.yaml
systemctl enable --now guardian
```

## Configuration

Edit `/etc/guardian/guardian.yaml`. Key sections:

**Thresholds** — all warn/critical pairs for CPU, memory, disk, swap, load, I/O latency, TCP connections, network errors.

**Alerts** — cooldown (dedup window), escalation time (WARN→CRITICAL), recovery notifications, and per-channel config.

**Collector** — polling interval (min 5s), top-N process tracking, EC2 IMDS timeout, spot interruption polling.

**Storage** — log directory, SQLite path, rotation size, retention days.

**API** — host/port for the REST API, optional bearer token auth.

Generate a fully commented config:

```bash
guardianctl init --output /etc/guardian/guardian.yaml
```

## Alert Channel Setup

### Slack

1. Create an Incoming Webhook in your Slack workspace
2. Set `alerts.slack.enabled: true` and paste the webhook URL, or export `GUARDIAN_SLACK_WEBHOOK=https://...`

### Telegram

1. Create a bot via `@BotFather` — note the token
2. Get your chat ID by messaging the bot and hitting `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Set `alerts.telegram.enabled: true`, `bot_token`, `chat_id`, or use `GUARDIAN_TELEGRAM_TOKEN` / `GUARDIAN_TELEGRAM_CHAT_ID`

### Email (Gmail)

1. Enable 2FA and create an App Password at `myaccount.google.com/security`
2. Set `alerts.email.enabled: true`, `smtp_user`, `smtp_password`, `from_addr`, `to_addrs`
3. Or export `GUARDIAN_EMAIL_PASSWORD=<app-password>`

## guardianctl Command Reference

```
guardianctl status                   # daemon status, uptime, active alerts
guardianctl metrics                  # all collector snapshots
guardianctl metrics -c cpu           # single collector detail
guardianctl metrics --watch          # live refresh (every 5s)
guardianctl top                      # htop-style live view
guardianctl alerts                   # recent alerts
guardianctl alerts --active          # currently firing alerts
guardianctl alerts --severity CRITICAL --since 1h
guardianctl test-alert --channel slack --severity WARN
guardianctl health                   # app health check results
guardianctl config show              # current config (secrets redacted)
guardianctl config validate          # validate config file
guardianctl config reload            # hot reload config (SIGHUP)
guardianctl init --output ./g.yaml   # generate example config
guardianctl logs -f                  # tail daemon logs
guardianctl logs -n 100 --level CRITICAL
guardianctl install                  # install systemd service (root)
guardianctl uninstall                # remove service (root)
guardianctl start/stop/restart       # manage daemon via systemd
```

## Adding a New Collector

Implement `guardian.collector.base.BaseCollector`:

```python
from guardian.collector.base import BaseCollector, MetricSnapshot
import time

class MyCollector(BaseCollector):
    name = "my_collector"

    def collect(self) -> MetricSnapshot:
        try:
            metrics = {"my_metric": 42}
            return MetricSnapshot(collector_name=self.name, timestamp=time.time(), metrics=metrics)
        except Exception as exc:
            return MetricSnapshot(collector_name=self.name, timestamp=time.time(), metrics={}, status="error", error=str(exc))
```

Register it in `GuardianDaemon._init_collectors()` in `guardian/main.py`.

Then add threshold evaluation rules in `AlertRouter.evaluate()` in `guardian/alerter/router.py`.

## Prometheus / Grafana (Phase 3)

The `guardian/exposition/prometheus.py` stub defines the interface. Phase 3 will implement `/metrics` in Prometheus text format on port 9732. Until then, use the REST API at `:9731/api/v1/metrics` to scrape metrics from external tooling.

## License

See LICENSE file.
