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
from rich.layout import Layout
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

    panel_content = (
        f"[bold]Status:[/]    ● Running\n"
        f"[bold]Uptime:[/]    {uptime_str}\n"
        f"[bold]Version:[/]   {data.get('version', '?')}\n"
        f"[bold]Instance:[/]  {data.get('instance_name', '')} ({data.get('instance_id', '')})\n"
        f"[bold]Environment:[/] {data.get('environment', '')}\n"
        f"[bold]Active Alerts:[/] {active_count}"
    )
    console.print(Panel(panel_content, title="GuardianD Status", expand=False))

    table = Table(title="Collectors")
    table.add_column("Name")
    table.add_column("Last Collected")
    table.add_column("Status")
    for c in data.get("collectors", []):
        ts = c.get("last_collected", 0)
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S") if ts else "—"
        status_color = "green" if c.get("status") == "ok" else "red"
        table.add_row(c["name"], ts_str, f"[{status_color}]{c.get('status', '?')}[/]")
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
    """Live top-like system view."""
    def _build() -> Table:
        data = _get(ctx, "/api/v1/metrics")
        snaps = (data or {}).get("snapshots", {})
        table = Table(title="GuardianD Live Top")
        table.add_column("Metric", style="bold")
        table.add_column("Value")

        cpu = snaps.get("cpu", {})
        mem = snaps.get("memory", {})
        disk = snaps.get("disk", {})

        table.add_row("CPU %", f"{cpu.get('percent_total', '?'):.1f}%" if isinstance(cpu.get('percent_total'), float) else "?")
        table.add_row("Memory %", f"{mem.get('percent_used', '?'):.1f}%" if isinstance(mem.get('percent_used'), float) else "?")
        table.add_row("Load (1m)", str(cpu.get("load_avg_1m", "?")))

        proc = snaps.get("process", {})
        table.add_section()
        table.add_row("[bold]Top Processes (CPU)[/]", "")
        for p in (proc.get("top_cpu") or [])[:5]:
            table.add_row(f"  {p.get('name', '')} ({p.get('pid', '')})", f"CPU={p.get('cpu_percent', 0):.1f}% MEM={p.get('memory_percent', 0):.1f}%")
        return table

    with Live(refresh_per_second=0.5) as live:
        while True:
            live.update(_build())
            time.sleep(2)


# --- Alerts ---

@cli.command("alerts")
@click.option("--since", default=None, help='ISO datetime or relative like "1h", "24h"')
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
            # parse relative like "1h"
            if since.endswith("h"):
                ts = time.time() - int(since[:-1]) * 3600
                params += f"&since={ts}"
            elif since.endswith("m"):
                ts = time.time() - int(since[:-1]) * 60
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
    if result and result.get("sent"):
        console.print(f"[green]Test alert sent to {result.get('channel')}[/]")
    else:
        console.print("[red]Test alert failed to send[/]")


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
        import yaml
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
def logs(follow: bool, lines: int, level: Optional[str]) -> None:
    """Show daemon logs."""
    log_path = "/var/log/guardian/guardian.log"
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
