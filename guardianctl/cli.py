from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import click
import requests
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

_DEFAULT_CONFIG = "/etc/guardian/guardian.yaml"
_DEFAULT_API_URL = "http://127.0.0.1:9731"

_SEV_COLORS = {
    "INFO": "green",
    "WARN": "yellow",
    "CRITICAL": "red",
    "EMERGENCY": "bright_red bold",
}


def _api_url_from_config(config_path: str) -> str:
    try:
        import yaml
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        api = raw.get("api", {})
        host = api.get("host", "127.0.0.1")
        port = api.get("port", 9731)
        return f"http://{host}:{port}"
    except Exception:
        return _DEFAULT_API_URL


def _get(ctx: click.Context, path: str) -> Optional[dict]:
    base = ctx.obj["api_url"]
    token = ctx.obj.get("token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(f"{base}{path}", headers=headers, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        console.print("[bold red]Error:[/] Cannot connect to GuardianD. Is the daemon running?")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)


def _post(ctx: click.Context, path: str, data: dict) -> Optional[dict]:
    base = ctx.obj["api_url"]
    token = ctx.obj.get("token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.post(f"{base}{path}", json=data, headers=headers, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        console.print("[bold red]Error:[/] Cannot connect to GuardianD. Is the daemon running?")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)


def _human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _parse_relative_time(since: str) -> float:
    if since.endswith("h"):
        return time.time() - int(since[:-1]) * 3600
    if since.endswith("m"):
        return time.time() - int(since[:-1]) * 60
    if since.endswith("d"):
        return time.time() - int(since[:-1]) * 86400
    try:
        return float(since)
    except ValueError:
        return time.time() - 3600


@click.group()
@click.option("--config", default=_DEFAULT_CONFIG, envvar="GUARDIAN_CONFIG", help="Config file path")
@click.option("--api-url", default=None, envvar="GUARDIAN_API_URL", help="API base URL")
@click.pass_context
def cli(ctx: click.Context, config: str, api_url: Optional[str]) -> None:
    ctx.ensure_object(dict)
    resolved_url = api_url or _api_url_from_config(config)
    ctx.obj["api_url"] = resolved_url
    ctx.obj["config"] = config
    ctx.obj["token"] = os.environ.get("GUARDIAN_API_TOKEN", "")


# --- Daemon management ---

@cli.command("start")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground instead of via systemd")
@click.option("--config", default=_DEFAULT_CONFIG)
@click.pass_context
def start(ctx: click.Context, foreground: bool, config: str) -> None:
    """Start the GuardianD daemon."""
    if foreground:
        from guardian.main import GuardianDaemon
        daemon = GuardianDaemon(config)
        daemon.start()
    else:
        result = subprocess.run(["systemctl", "start", "guardian"], capture_output=True, text=True)
        if result.returncode == 0:
            console.print("[green]GuardianD started.[/]")
        else:
            console.print(f"[red]Failed to start:[/] {result.stderr.strip()}")
            sys.exit(1)


@cli.command("stop")
def stop() -> None:
    """Stop the GuardianD daemon."""
    result = subprocess.run(["systemctl", "stop", "guardian"], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]GuardianD stopped.[/]")
    else:
        console.print(f"[red]Failed to stop:[/] {result.stderr.strip()}")
        sys.exit(1)


@cli.command("restart")
def restart() -> None:
    """Restart the GuardianD daemon."""
    result = subprocess.run(["systemctl", "restart", "guardian"], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]GuardianD restarted.[/]")
    else:
        console.print(f"[red]Failed to restart:[/] {result.stderr.strip()}")
        sys.exit(1)


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon status."""
    data = _get(ctx, "/api/v1/status")
    if not data:
        return

    uptime_str = _human_uptime(data.get("uptime_seconds", 0))
    active_count = data.get("active_alert_count", 0)

    intel = data.get("intelligence", {})
    intel_str = "disabled"
    if intel.get("enabled"):
        intel_str = "warming up" if intel.get("warming_up") else "active"

    prom = data.get("prometheus", {})
    prom_str = f"Listening :{prom.get('port', 9732)}" if prom.get("enabled") else "disabled"

    panel_content = (
        f"[bold]Status:[/]        ● Running\n"
        f"[bold]Uptime:[/]        {uptime_str}\n"
        f"[bold]Version:[/]       {data.get('version', '?')}\n"
        f"[bold]Instance:[/]      {data.get('instance_name', '')} ({data.get('instance_id', '')})\n"
        f"[bold]Environment:[/]   {data.get('environment', '')}\n"
        f"[bold]Intelligence:[/]  {intel_str}\n"
        f"[bold]Prometheus:[/]    {prom_str}\n"
        f"[bold]Active Alerts:[/] {active_count}"
    )
    console.print(Panel(panel_content, title="GuardianD Status", expand=False))

    table = Table(title="Collectors")
    table.add_column("Name")
    table.add_column("Last Collected")
    table.add_column("Status")
    table.add_column("Duration")
    for c in data.get("collectors", []):
        ts = c.get("last_collected", 0)
        if ts:
            age = time.time() - ts
            ts_str = f"{age:.1f}s ago"
        else:
            ts_str = "—"
        status_color = "green" if c.get("status") == "ok" else "red"
        dur = c.get("duration_ms", 0)
        table.add_row(
            c["name"],
            ts_str,
            f"[{status_color}]{c.get('status', '?')}[/]",
            f"{dur:.1f}ms" if dur else "—",
        )
    console.print(table)


# --- Metrics ---

@cli.command("metrics")
@click.option("--collector", "-c", default=None, help="Specific collector name")
@click.option("--watch", "-w", is_flag=True, help="Refresh continuously")
@click.option("--interval", "-i", default=5, help="Refresh interval (seconds)")
@click.pass_context
def metrics(ctx: click.Context, collector: Optional[str], watch: bool, interval: int) -> None:
    """Display current metrics."""
    def _render() -> Table:
        if collector:
            data = _get(ctx, f"/api/v1/metrics/{collector}")
            table = Table(title=f"Metrics: {collector}")
            table.add_column("Key")
            table.add_column("Value")
            if data and data.get("metrics"):
                for k, v in data["metrics"].items():
                    table.add_row(str(k), str(v))
        else:
            data = _get(ctx, "/api/v1/metrics")
            table = Table(title="All Metrics (summary)")
            table.add_column("Collector")
            table.add_column("Key Metrics")
            for name, snap_metrics in (data or {}).get("snapshots", {}).items():
                summary = ", ".join(f"{k}={v}" for k, v in list(snap_metrics.items())[:3])
                table.add_row(name, summary[:80])
        return table

    if watch:
        with Live(refresh_per_second=1) as live:
            while True:
                live.update(_render())
                time.sleep(interval)
    else:
        console.print(_render())


@cli.command("top")
@click.pass_context
def top(ctx: click.Context) -> None:
    """Live top-like system view with PSI indicators."""
    def _build() -> Table:
        data = _get(ctx, "/api/v1/metrics")
        snaps = (data or {}).get("snapshots", {})
        table = Table(title="GuardianD Live Top")
        table.add_column("Metric", style="bold")
        table.add_column("Value")

        cpu = snaps.get("cpu", {})
        mem = snaps.get("memory", {})
        sys_ev = snaps.get("system_events", {})

        # PSI header row
        psi = sys_ev.get("psi", {}) if sys_ev else {}
        cpu_psi = psi.get("cpu", {}).get("some_avg10", -1.0)
        mem_psi = psi.get("memory", {}).get("some_avg10", -1.0)
        io_psi = psi.get("io", {}).get("some_avg10", -1.0)

        def _psi_str(val: float, name: str) -> str:
            if val < 0:
                return f"{name} PSI: N/A"
            color = "green" if val < 10 else ("yellow" if val < 30 else "red")
            return f"{name} PSI: [{color}]{val:.1f}%[/]"

        table.add_row(
            "PSI",
            f"{_psi_str(cpu_psi, 'CPU')} | {_psi_str(mem_psi, 'Mem')} | {_psi_str(io_psi, 'I/O')}",
        )
        table.add_section()

        cpu_pct = cpu.get("percent_total", "?")
        table.add_row("CPU %", f"{cpu_pct:.1f}%" if isinstance(cpu_pct, float) else str(cpu_pct))
        mem_pct = mem.get("percent_used", "?")
        table.add_row("Memory %", f"{mem_pct:.1f}%" if isinstance(mem_pct, float) else str(mem_pct))
        table.add_row("Load (1m)", str(cpu.get("load_avg_1m", "?")))
        table.add_row("iowait %", str(cpu.get("times_iowait", "?")))
        table.add_row("steal %", str(cpu.get("times_steal", "?")))

        proc = snaps.get("process", {})
        table.add_section()
        table.add_row("[bold]Top Processes (CPU)[/]", "")
        for p in (proc.get("top_cpu") or [])[:5]:
            table.add_row(
                f"  {p.get('name', '')} ({p.get('pid', '')})",
                f"CPU={p.get('cpu_percent', 0):.1f}% MEM={p.get('memory_percent', 0):.1f}%",
            )
        return table

    with Live(refresh_per_second=0.5) as live:
        while True:
            live.update(_build())
            time.sleep(2)


# --- Alerts ---

@cli.command("alerts")
@click.option("--since", default=None, help='Relative like "1h", "24h", or unix timestamp')
@click.option("--severity", default=None, type=click.Choice(["INFO", "WARN", "CRITICAL", "EMERGENCY"]))
@click.option("--limit", default=50)
@click.option("--active", is_flag=True, help="Show only active alerts")
@click.pass_context
def alerts(ctx: click.Context, since: Optional[str], severity: Optional[str], limit: int, active: bool) -> None:
    """Show alerts."""
    if active:
        data = _get(ctx, "/api/v1/alerts/active")
        alert_list = (data or {}).get("alerts", [])
    else:
        params = f"?limit={limit}"
        if severity:
            params += f"&severity={severity}"
        if since:
            ts = _parse_relative_time(since)
            params += f"&since={ts}"
        data = _get(ctx, f"/api/v1/alerts{params}")
        alert_list = (data or {}).get("alerts", [])

    table = Table(title="Alerts")
    table.add_column("Severity")
    table.add_column("Category")
    table.add_column("Title")
    table.add_column("Time")
    for a in alert_list:
        sev = a.get("severity", "INFO")
        color = _SEV_COLORS.get(sev, "white")
        ts = a.get("timestamp") or a.get("first_seen", 0)
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M") if ts else "?"
        table.add_row(f"[{color}]{sev}[/]", a.get("category", ""), a.get("title", ""), ts_str)
    console.print(table)


@cli.command("test-alert")
@click.option("--channel", type=click.Choice(["slack", "telegram", "email", "all"]), default="all")
@click.option("--severity", type=click.Choice(["INFO", "WARN", "CRITICAL", "EMERGENCY"]), default="WARN")
@click.pass_context
def test_alert(ctx: click.Context, channel: str, severity: str) -> None:
    """Fire a test alert."""
    result = _post(ctx, "/api/v1/alerts/test", {"severity": severity, "channel": channel})
    if not result:
        ctx.exit(1)

    results = result.get("results")
    if not isinstance(results, dict):
        # Older daemon without per-channel reporting: fall back to the boolean.
        if result.get("sent"):
            console.print(f"[green]Test alert sent to {result.get('channel')}[/]")
            return
        console.print("[red]Test alert failed to send[/]")
        ctx.exit(1)

    if not results:
        console.print(f"[red]No enabled channel matches '{channel}'.[/]")
        ctx.exit(1)

    labels = {
        "sent": "[green]✓ sent[/]",
        "failed": "[red]✗ failed[/]",
        "skipped_below_severity": f"[yellow]– skipped (below this channel's min_severity, alert was {severity})[/]",
        "not_enabled": "[yellow]– not enabled[/]",
    }
    for name, outcome in results.items():
        console.print(f"  {name}: {labels.get(outcome, outcome)}")

    if any(o == "failed" for o in results.values()):
        ctx.exit(1)
    if not any(o == "sent" for o in results.values()):
        # Nothing actually delivered (all skipped / not enabled).
        ctx.exit(1)


# --- Health Checks ---

@cli.command("health")
@click.pass_context
def health(ctx: click.Context) -> None:
    """Show app health check results."""
    data = _get(ctx, "/api/v1/health-checks")
    table = Table(title="Health Checks")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Healthy")
    table.add_column("Latency")
    table.add_column("Error")
    for chk in (data or {}).get("checks", []):
        healthy = chk.get("healthy", False)
        color = "green" if healthy else "red"
        latency = f"{chk.get('latency_ms', 0):.0f}ms"
        table.add_row(
            chk.get("name", ""),
            chk.get("type", ""),
            chk.get("target", ""),
            f"[{color}]{'✓' if healthy else '✗'}[/]",
            latency,
            chk.get("error", "")[:60],
        )
    summary = data or {}
    console.print(table)
    console.print(f"Healthy: {summary.get('healthy_count', 0)}/{summary.get('total_count', 0)}")


# --- Configuration ---

@cli.command("config")
@click.argument("subcommand", type=click.Choice(["show", "validate", "reload"]))
@click.pass_context
def config_cmd(ctx: click.Context, subcommand: str) -> None:
    """Manage configuration."""
    if subcommand == "show":
        data = _get(ctx, "/api/v1/config")
        import json
        console.print(json.dumps(data, indent=2))
    elif subcommand == "validate":
        from guardian.config.loader import validate_config, load_config
        config_path = ctx.obj["config"]
        try:
            cfg = load_config(config_path)
            errors = validate_config(cfg)
            if errors:
                for e in errors:
                    console.print(f"[red]✗[/] {e}")
                sys.exit(1)
            else:
                console.print("[green]Config is valid.[/]")
        except Exception as exc:
            console.print(f"[red]Config error:[/] {exc}")
            sys.exit(1)
    elif subcommand == "reload":
        result = _post(ctx, "/api/v1/control/reload", {})
        console.print(f"[green]{result}[/]")


@cli.command("init")
@click.option("--output", default="./guardian.yaml")
def init(output: str) -> None:
    """Generate a default config file."""
    from guardian.config.loader import generate_default_config
    generate_default_config(output)
    console.print(f"[green]Config written to {output}[/]")
    console.print()
    console.print("[bold]Next steps:[/]")
    console.print(f"  1. [cyan]Set instance name:[/]  edit [bold]{output}[/]  →  instance_name: my-server")
    console.print(f"  2. [cyan]Enable Telegram:[/]    guardianctl setup telegram --config {output}")
    console.print(f"  3. [cyan]Validate config:[/]    guardianctl --config {output} config validate")
    console.print(f"  4. [cyan]Start daemon:[/]       guardiand --config {output}")
    console.print(f"  5. [cyan]Check status:[/]       guardianctl --config {output} status")
    console.print()
    console.print("[dim]Note: at least one alert channel must be enabled before the daemon will accept the config.[/]")


@cli.command("install")
@click.option("--config", default=_DEFAULT_CONFIG)
@click.option("--systemd/--no-systemd", default=True)
def install(config: str, systemd: bool) -> None:
    """Install GuardianD as a systemd service."""
    if os.geteuid() != 0:
        console.print("[red]Error:[/] install requires root (sudo)")
        sys.exit(1)

    dirs = ["/etc/guardian", "/var/log/guardian", "/var/lib/guardian", "/var/run/guardian"]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        console.print(f"Created {d}")

    if not os.path.exists(config):
        from guardian.config.loader import generate_default_config
        generate_default_config(config)
        console.print(f"Config generated at {config}")

    if systemd:
        src = os.path.join(os.path.dirname(__file__), "..", "systemd", "guardian.service")
        if os.path.exists(src):
            import shutil
            shutil.copy(src, "/etc/systemd/system/guardian.service")
            subprocess.run(["systemctl", "daemon-reload"])
            console.print("[green]Systemd service installed.[/]")
            console.print("Run: systemctl enable --now guardian")
        else:
            console.print("[yellow]Warning:[/] guardian.service not found")


@cli.command("uninstall")
@click.option("--remove-data", is_flag=True, help="Also remove log and data directories")
def uninstall(remove_data: bool) -> None:
    """Remove GuardianD systemd service."""
    if os.geteuid() != 0:
        console.print("[red]Error:[/] uninstall requires root (sudo)")
        sys.exit(1)
    subprocess.run(["systemctl", "stop", "guardian"], capture_output=True)
    subprocess.run(["systemctl", "disable", "guardian"], capture_output=True)
    service_path = "/etc/systemd/system/guardian.service"
    if os.path.exists(service_path):
        os.remove(service_path)
    subprocess.run(["systemctl", "daemon-reload"])
    if remove_data:
        import shutil
        for d in ["/var/log/guardian", "/var/lib/guardian"]:
            shutil.rmtree(d, ignore_errors=True)
    console.print("[green]GuardianD uninstalled.[/]")


# --- Logs ---

@cli.command("logs")
@click.option("--follow", "-f", is_flag=True)
@click.option("--lines", "-n", default=50)
@click.option("--level", default=None, type=click.Choice(["INFO", "WARN", "CRITICAL", "EMERGENCY"]))
@click.pass_context
def logs(ctx: click.Context, follow: bool, lines: int, level: Optional[str]) -> None:
    """Show daemon logs."""
    log_dir = "/var/log/guardian"
    config_path = ctx.obj.get("config", _DEFAULT_CONFIG)
    try:
        import yaml
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        log_dir = raw.get("storage", {}).get("log_dir", log_dir)
    except Exception:
        pass
    log_path = os.path.join(log_dir, "guardian.log")
    if not os.path.exists(log_path):
        console.print(f"[yellow]Log file not found:[/] {log_path}")
        sys.exit(1)

    def _print_line(line: str) -> None:
        if level and f"[{level}]" not in line:
            return
        color = "white"
        for sev, clr in _SEV_COLORS.items():
            if f"[{sev}]" in line:
                color = clr
                break
        console.print(f"[{color}]{line.rstrip()}[/]")

    if follow:
        with open(log_path) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    _print_line(line)
                else:
                    time.sleep(0.1)
    else:
        with open(log_path) as f:
            all_lines = f.readlines()
        for line in all_lines[-lines:]:
            _print_line(line)


# --- Phase 2/4 Intelligence commands ---

@cli.command("anomalies")
@click.option("--since", default="1h", help='Time range, e.g. "1h", "24h"')
@click.option("--min-score", default=2.0, type=float, help="Minimum z-score to show")
@click.pass_context
def anomalies(ctx: click.Context, since: str, min_score: float) -> None:
    """Show anomaly detections from the intelligence layer."""
    ts = _parse_relative_time(since)
    data = _get(ctx, f"/api/v1/intelligence/anomalies?since={ts}")
    items = (data or {}).get("anomalies", [])

    if not items:
        console.print("[green]No anomalies detected in this time range.[/]")
        return

    table = Table(title=f"Anomalies (min score: {min_score})")
    table.add_column("Time")
    table.add_column("Metric / Title")
    table.add_column("Z-Score")
    table.add_column("Severity")

    for a in items:
        score = a.get("anomaly_score") or 0.0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if score < min_score:
            continue
        ts_val = a.get("timestamp", 0)
        ts_str = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%H:%M:%S") if ts_val else "?"
        color = "red" if score >= 3.0 else "yellow"
        sev = a.get("severity", "WARN")
        sev_color = _SEV_COLORS.get(sev, "white")
        table.add_row(
            ts_str,
            a.get("title", ""),
            f"[{color}]{score:.2f}σ[/]",
            f"[{sev_color}]{sev}[/]",
        )
    console.print(table)


@cli.command("forecast")
@click.pass_context
def forecast(ctx: click.Context) -> None:
    """Show trend forecasts (disk/memory fill predictions)."""
    data = _get(ctx, "/api/v1/intelligence/anomalies?since=0")
    items = (data or {}).get("anomalies", [])

    forecast_items = [a for a in items if a.get("forecast_eta_minutes")]
    if not forecast_items:
        console.print("[green]No active forecasts.[/]")
        return

    table = Table(title="Trend Forecasts")
    table.add_column("Metric")
    table.add_column("ETA")
    table.add_column("Severity")

    for a in forecast_items:
        eta_min = float(a.get("forecast_eta_minutes", 0))
        eta_h = eta_min / 60
        color = "red" if eta_h < 2 else ("yellow" if eta_h < 8 else "green")
        eta_str = f"[{color}]{eta_h:.1f}h[/]" if eta_h >= 1 else f"[{color}]{eta_min:.0f}m[/]"
        sev = a.get("severity", "WARN")
        sev_color = _SEV_COLORS.get(sev, "white")
        table.add_row(a.get("title", ""), eta_str, f"[{sev_color}]{sev}[/]")
    console.print(table)


@cli.command("baseline")
@click.option("--collector", "-c", required=True, help="Collector name (e.g. cpu, memory)")
@click.option("--metric", "-m", default=None, help="Specific metric key")
@click.pass_context
def baseline(ctx: click.Context, collector: str, metric: Optional[str]) -> None:
    """Show baseline statistics for a collector."""
    path = f"/api/v1/intelligence/baselines?collector={collector}"
    if metric:
        path += f"&metric={metric}"
    data = _get(ctx, path)

    if metric:
        stats = (data or {}).get("stats")
        if not stats:
            console.print(f"[yellow]No baseline data for {collector}.{metric}[/]")
            return
        table = Table(title=f"Baseline: {collector}.{metric}")
        table.add_column("Stat")
        table.add_column("Value")
        for k, v in stats.items():
            table.add_row(str(k), str(v))
        console.print(table)
    else:
        baselines = (data or {}).get("baselines", {})
        if not baselines:
            console.print(f"[yellow]No baseline data for {collector}[/]")
            return
        table = Table(title=f"Baselines: {collector}")
        table.add_column("Metric")
        table.add_column("Mean")
        table.add_column("Stddev")
        table.add_column("P95")
        table.add_column("P99")
        table.add_column("Samples")
        table.add_column("Ready")
        for mkey, stats in baselines.items():
            ready_color = "green" if stats.get("is_ready") else "yellow"
            table.add_row(
                mkey,
                f"{stats.get('mean', 0):.2f}",
                f"{stats.get('stddev', 0):.2f}",
                f"{stats.get('p95', 0):.2f}",
                f"{stats.get('p99', 0):.2f}",
                str(stats.get("sample_count", 0)),
                f"[{ready_color}]{'✓' if stats.get('is_ready') else '✗'}[/]",
            )
        console.print(table)


@cli.command("diagnose")
@click.pass_context
def diagnose(ctx: click.Context) -> None:
    """Run bottleneck fingerprinter against current metrics and show diagnosis."""
    data = _get(ctx, "/api/v1/metrics")
    snaps_raw = (data or {}).get("snapshots", {})

    try:
        from guardian.config.schema import GuardianConfig
        from guardian.intelligence.fingerprint import BottleneckFingerprinter
        from guardian.collector.base import MetricSnapshot
        import time as _time

        cfg = GuardianConfig()
        fp = BottleneckFingerprinter(cfg)

        snaps = {
            name: MetricSnapshot(
                collector_name=name,
                timestamp=_time.time(),
                metrics=metrics_dict,
            )
            for name, metrics_dict in snaps_raw.items()
        }
        findings = fp.analyze(snaps)
    except Exception as exc:
        console.print(f"[red]Diagnose error:[/] {exc}")
        return

    if not findings:
        console.print(Panel("[green]No bottleneck patterns detected.[/]", title="Diagnosis"))
        return

    table = Table(title="Bottleneck Diagnosis")
    table.add_column("Pattern")
    table.add_column("Diagnosis")
    table.add_column("Confidence")
    for f in findings:
        conf = f.get("confidence", 0.0)
        color = "red" if conf > 0.7 else ("yellow" if conf > 0.4 else "white")
        table.add_row(
            f.get("pattern", ""),
            f.get("diagnosis", ""),
            f"[{color}]{conf:.0%}[/]",
        )
    console.print(table)


@cli.command("prometheus")
@click.argument("subcommand", type=click.Choice(["status", "url"]))
@click.pass_context
def prometheus_cmd(ctx: click.Context, subcommand: str) -> None:
    """Show Prometheus exposition status."""
    data = _get(ctx, "/api/v1/status")
    prom = (data or {}).get("prometheus", {})
    enabled = prom.get("enabled", False)
    host = prom.get("host", "0.0.0.0")
    port = prom.get("port", 9732)

    if subcommand == "status":
        color = "green" if enabled else "yellow"
        console.print(f"Prometheus: [{color}]{'enabled' if enabled else 'disabled'}[/]")
        if enabled:
            console.print(f"Scrape URL: http://{host}:{port}/metrics")
    elif subcommand == "url":
        if enabled:
            console.print(f"http://{host}:{port}/metrics")
        else:
            console.print("[yellow]Prometheus exposition is disabled.[/]")


# --- Setup wizards ---

@cli.group("setup")
def setup_group() -> None:
    """Interactive setup wizards for alert channels."""


@setup_group.command("telegram")
@click.option("--config", "config_path", default=_DEFAULT_CONFIG, help="Config file to update")
def setup_telegram(config_path: str) -> None:
    """Interactive wizard: configure Telegram alerts step-by-step."""
    console.print(Panel.fit(
        "[bold cyan]GuardianD — Telegram Setup Wizard[/]\n\n"
        "You need a Telegram bot token and a chat ID.\n"
        "Steps:\n"
        "  1. Open Telegram → search [bold]@BotFather[/]\n"
        "  2. Send [bold]/newbot[/], follow prompts → copy the [bold]API token[/]\n"
        "  3. Start a chat with your new bot (or add it to a group)\n"
        "  4. Send any message to the bot / group\n"
        "  5. Come back here — we'll auto-detect your chat ID",
        title="Setup"
    ))

    bot_token = click.prompt("\nPaste your bot token").strip()
    if not bot_token or ":" not in bot_token:
        console.print("[red]Invalid token format. Expected: 1234567890:ABCdef...[/]")
        return

    # Validate token by calling getMe
    console.print("\n[dim]Validating token...[/]")
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=10
        )
        data = resp.json()
        if not data.get("ok"):
            console.print(f"[red]Token rejected by Telegram:[/] {data.get('description', 'unknown error')}")
            return
        bot = data["result"]
        console.print(f"[green]✓ Bot verified:[/] @{bot.get('username')} ({bot.get('first_name')})")
    except Exception as e:
        console.print(f"[red]Could not reach Telegram API:[/] {e}")
        return

    # Get updates to find chat_id
    console.print("\n[dim]Fetching recent messages to detect your chat ID...[/]")
    console.print("[yellow]  → Make sure you've sent at least one message to the bot[/]")
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"limit": 10, "timeout": 0},
            timeout=15
        )
        data = resp.json()
        updates = data.get("result", [])
    except Exception as e:
        console.print(f"[red]Could not fetch updates:[/] {e}")
        updates = []

    chat_id = ""
    if updates:
        chats: dict = {}
        for upd in updates:
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat", {})
            if chat.get("id"):
                cid = str(chat["id"])
                ctype = chat.get("type", "?")
                ctitle = chat.get("title") or chat.get("username") or chat.get("first_name") or cid
                chats[cid] = f"{ctitle} ({ctype})"

        if chats:
            console.print("\nDetected chats:")
            items = list(chats.items())
            for i, (cid, name) in enumerate(items, 1):
                console.print(f"  [{i}] {name}  [dim]chat_id={cid}[/]")
            if len(items) == 1:
                chat_id = items[0][0]
                console.print(f"\n[green]Auto-selected:[/] {chat_id}")
            else:
                idx = click.prompt("Select chat number", type=click.IntRange(1, len(items)), default=1)
                chat_id = items[idx - 1][0]

    if not chat_id:
        console.print("\n[yellow]No messages detected.[/] Enter chat ID manually:")
        console.print("  (Find it at api.telegram.org/bot{TOKEN}/getUpdates after sending a message)")
        chat_id = click.prompt("Chat ID").strip()

    # Send a real test message
    console.print(f"\n[dim]Sending test message to chat {chat_id}...[/]")
    try:
        test_msg = (
            r"✅ *GuardianD connected\!*" + "\n\n"
            r"Your Telegram alerting is configured correctly\." + "\n"
            r"You will receive alerts here when thresholds are breached\."
        )
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ GuardianD connected! Telegram alerting is working.", "parse_mode": None},
            timeout=10
        )
        if resp.status_code == 200:
            console.print("[green]✓ Test message sent! Check your Telegram.[/]")
        else:
            err = resp.json().get("description", resp.text[:200])
            console.print(f"[red]Send failed:[/] {err}")
            return
    except Exception as e:
        console.print(f"[red]Send error:[/] {e}")
        return

    # Update config file
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path) as f:
                cfg_data = yaml.safe_load(f) or {}
            if "alerts" not in cfg_data:
                cfg_data["alerts"] = {}
            cfg_data["alerts"]["telegram"] = {
                "enabled": True,
                "bot_token": bot_token,
                "chat_id": chat_id,
                "min_severity": "WARN",
            }
            with open(config_path, "w") as f:
                yaml.dump(cfg_data, f, default_flow_style=False, allow_unicode=True)
            console.print(f"\n[green]✓ Config updated:[/] {config_path}")
            console.print("  Reload daemon:  [bold]guardianctl config reload[/]")
            console.print("  Test alert:     [bold]guardianctl test-alert --channel telegram[/]")
        except Exception as e:
            console.print(f"\n[yellow]Could not update config:[/] {e}")
            console.print(f"Add manually to {config_path}:")
            console.print(f"  alerts.telegram.enabled: true")
            console.print(f"  alerts.telegram.bot_token: {bot_token}")
            console.print(f"  alerts.telegram.chat_id: {chat_id}")
    else:
        console.print(f"\n[yellow]Config file not found:[/] {config_path}")
        console.print("  Run [bold]guardianctl init --output /path/to/guardian.yaml[/] first.")


@setup_group.command("openai")
@click.option("--config", "config_path", default=_DEFAULT_CONFIG, help="Config file to update")
def setup_openai(config_path: str) -> None:
    """Interactive wizard: configure AI-assisted alerts (OpenAI key) step-by-step."""
    console.print(Panel.fit(
        "[bold cyan]GuardianD — AI Alert Setup Wizard[/]\n\n"
        "Adds a plain-English interpretation + quick-fix steps to every alert,\n"
        "on all channels. You need an OpenAI (or OpenAI-compatible) API key.\n"
        "Steps:\n"
        "  1. Get a key at [bold]platform.openai.com/api-keys[/]\n"
        "  2. Paste it below — we'll verify it with a real test call\n"
        "  (For Azure / a local gateway, change the base URL when asked.)",
        title="Setup"
    ))

    api_key = click.prompt("\nPaste your API key", hide_input=True).strip()
    if not api_key:
        console.print("[red]No key entered.[/]")
        return
    base_url = click.prompt("API base URL", default="https://api.openai.com/v1").strip().rstrip("/")
    model = click.prompt("Model", default="gpt-4o-mini").strip()
    min_severity = click.prompt(
        "Minimum severity to enrich",
        type=click.Choice(["INFO", "WARN", "CRITICAL", "EMERGENCY"]),
        default="WARN",
    )

    # Validate by making a real (tiny) chat completion — proves key + model + endpoint.
    console.print("\n[dim]Verifying key with a test request...[/]")
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: GuardianD AI connected."}],
                "max_tokens": 20,
                "temperature": 0,
            },
            timeout=20,
        )
    except Exception as e:
        console.print(f"[red]Could not reach the API:[/] {e}")
        return

    if resp.status_code == 401:
        console.print("[red]Key rejected (401 Unauthorized).[/] Double-check the key and try again.")
        return
    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        console.print(f"[red]API error ({resp.status_code}):[/] {err}")
        console.print("[yellow]If you're on Azure/a gateway, check the base URL and model name.[/]")
        return
    try:
        reply = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        reply = "(ok)"
    console.print(f"[green]✓ Verified.[/] Model [bold]{model}[/] replied: [dim]{reply}[/]")

    # Update config file
    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path) as f:
                cfg_data = yaml.safe_load(f) or {}
            cfg_data["ai"] = {
                "enabled": True,
                "provider": "openai",
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "timeout_seconds": 12,
                "max_tokens": 250,
                "include_metrics": True,
                "min_severity": min_severity,
                "cache_ttl_seconds": 1800,
            }
            with open(config_path, "w") as f:
                yaml.dump(cfg_data, f, default_flow_style=False, allow_unicode=True)
            try:
                os.chmod(config_path, 0o600)
            except OSError:
                pass
            console.print(f"\n[green]✓ Config updated:[/] {config_path} [dim](chmod 600 — holds a secret)[/]")
            console.print("  Reload daemon:  [bold]guardianctl config reload[/]")
            console.print("  Test alert:     [bold]guardianctl test-alert --severity CRITICAL[/]")
            console.print(
                "\n[dim]Prefer to keep the key out of the file? Remove api_key from the config and set\n"
                "  export GUARDIAN_OPENAI_API_KEY=… (env wins over the file).[/]"
            )
        except Exception as e:
            console.print(f"\n[yellow]Could not update config:[/] {e}")
            console.print(f"Add manually to {config_path} under a top-level [bold]ai:[/] section:")
            console.print("  enabled: true")
            console.print(f"  api_key: {api_key}")
            console.print(f"  base_url: {base_url}")
            console.print(f"  model: {model}")
    else:
        console.print(f"\n[yellow]Config file not found:[/] {config_path}")
        console.print("  Run [bold]guardianctl init --output /path/to/guardian.yaml[/] first.")


@setup_group.command("grafana")
@click.pass_context
def setup_grafana(ctx: click.Context) -> None:
    """Show Grafana setup instructions and Prometheus scrape config."""
    # Try to get Prometheus port from running daemon
    prom_port = 9732
    prom_host = "localhost"
    try:
        data = _get(ctx, "/api/v1/status")
        prom = (data or {}).get("prometheus", {})
        if prom.get("enabled"):
            prom_port = prom.get("port", 9732)
            prom_host = prom.get("host", "0.0.0.0")
            if prom_host in ("0.0.0.0", ""):
                prom_host = "localhost"
    except SystemExit:
        pass

    scrape_url = f"http://{prom_host}:{prom_port}/metrics"
    datasource_url = f"http://{prom_host}:{prom_port}"

    console.print(Panel.fit(
        "[bold cyan]GuardianD — Grafana Setup[/]",
        title="Setup"
    ))

    console.print("\n[bold]Step 1 — Enable Prometheus in guardian.yaml:[/]")
    console.print("""
  prometheus:
    enabled: true
    host: "0.0.0.0"
    port: 9732
""")

    console.print("[bold]Step 2 — Add Prometheus datasource in Grafana:[/]")
    console.print(f"  Datasource URL: [cyan]{datasource_url}[/]  [dim](base URL — not /metrics)[/]")
    console.print("  In Grafana: Configuration → Data Sources → Add → Prometheus")
    console.print(f"  Set URL to: {datasource_url}")
    console.print("  Click [bold]Save & Test[/]\n")

    console.print("[bold]Step 3 — Import the dashboard:[/]")
    dashboard_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "grafana", "dashboard.json"
    )
    dashboard_path = os.path.normpath(dashboard_path)
    console.print("  In Grafana: Dashboards → Import → Upload JSON file")
    console.print(f"  File: [cyan]{dashboard_path}[/]")
    console.print("  Select your Prometheus datasource → Import\n")

    console.print("[bold]Step 4 — Select your instance:[/]")
    console.print("  Use the [bold]Instance[/] dropdown at the top of the dashboard")
    console.print("  Your instance name is set in guardian.yaml → [bold]instance_name[/]\n")

    # Live check
    try:
        import urllib.request
        r = urllib.request.urlopen(scrape_url, timeout=3)
        console.print(f"[green]✓ Prometheus metrics endpoint reachable:[/] {scrape_url}")
        console.print(f"  {r.status} OK — Grafana can scrape this")
    except Exception:
        console.print(f"[yellow]⚠ Cannot reach {scrape_url}[/]")
        console.print("  Make sure daemon is running with prometheus.enabled: true")
