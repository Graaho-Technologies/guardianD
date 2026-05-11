from __future__ import annotations

import hashlib
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger
from .base import Alert, AlertSeverity, BaseAlerter

_log = get_logger(__name__)


def _fingerprint(category: str, title: str) -> str:
    return hashlib.sha256(f"{category}|{title}".encode()).hexdigest()[:16]


def _make_alert(
    severity: AlertSeverity,
    category: str,
    title: str,
    message: str,
    metrics: dict,  # type: ignore[type-arg]
    config: GuardianConfig,
    instance_id: str = "",
) -> Alert:
    fp = _fingerprint(category, title)
    return Alert(
        id=str(uuid.uuid4()),
        severity=severity,
        category=category,
        title=title,
        message=message,
        metrics=metrics,
        instance_id=instance_id,
        instance_name=config.instance_name or instance_id,
        environment=config.environment,
        timestamp=time.time(),
        fingerprint=fp,
    )


class AlertRouter:
    def __init__(self, config: GuardianConfig, alerters: List[BaseAlerter]) -> None:
        self.config = config
        self.alerters = alerters
        # fingerprint → (alert, first_seen_ts, last_sent_ts)
        self._active: Dict[str, Tuple[Alert, float, float]] = {}

    def _instance_id(self, snapshots: Dict[str, MetricSnapshot]) -> str:
        ec2 = snapshots.get("ec2")
        if ec2 and ec2.metrics.get("instance_id"):
            return ec2.metrics["instance_id"]
        import socket
        return socket.gethostname()

    def evaluate(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        alerts: List[Alert] = []
        t = self.config.thresholds
        instance_id = self._instance_id(snapshots)

        def alert(sev: AlertSeverity, cat: str, title: str, msg: str, metrics: dict) -> None:  # type: ignore[type-arg]
            alerts.append(_make_alert(sev, cat, title, msg, metrics, self.config, instance_id))

        # --- CPU ---
        cpu = snapshots.get("cpu")
        if cpu and cpu.status == "ok" and cpu.metrics:
            m = cpu.metrics
            pct = m.get("percent_total", 0)
            steal = m.get("times_steal", 0)
            iowait = m.get("times_iowait", 0)
            load_norm = m.get("load_avg_normalized_1m", 0)

            if pct >= t.cpu_critical:
                alert(AlertSeverity.CRITICAL, "cpu", "Critical CPU Usage",
                      f"CPU usage at {pct:.1f}%", {"cpu_percent": pct})
            elif pct >= t.cpu_warn:
                alert(AlertSeverity.WARN, "cpu", "High CPU Usage",
                      f"CPU usage at {pct:.1f}%", {"cpu_percent": pct})

            if steal >= t.cpu_steal_critical:
                alert(AlertSeverity.CRITICAL, "cpu", "Severe EC2 CPU Steal",
                      f"CPU steal at {steal:.1f}%", {"cpu_steal": steal})
            elif steal >= t.cpu_steal_warn:
                alert(AlertSeverity.WARN, "cpu", "EC2 CPU Steal Detected",
                      f"CPU steal at {steal:.1f}%", {"cpu_steal": steal})

            if iowait >= 60.0:
                alert(AlertSeverity.WARN, "cpu", "High I/O Wait",
                      f"iowait at {iowait:.1f}%", {"iowait": iowait})

            if load_norm >= t.load_avg_critical_multiplier:
                alert(AlertSeverity.CRITICAL, "cpu", "Critical System Load",
                      f"Normalized load {load_norm:.2f}", {"load_normalized": load_norm})
            elif load_norm >= t.load_avg_warn_multiplier:
                alert(AlertSeverity.WARN, "cpu", "High System Load",
                      f"Normalized load {load_norm:.2f}", {"load_normalized": load_norm})

        # --- Memory ---
        mem = snapshots.get("memory")
        if mem and mem.status == "ok" and mem.metrics:
            m = mem.metrics
            mem_pct = m.get("percent_used", 0)
            swap_pct = m.get("swap_percent", 0)
            swap_sout = m.get("swap_sout_per_sec", 0)
            oom_new = m.get("oom_kill_count_new", 0)

            if mem_pct >= t.memory_critical:
                alert(AlertSeverity.CRITICAL, "memory", "Critical Memory Usage",
                      f"Memory at {mem_pct:.1f}%", {"memory_percent": mem_pct})
            elif mem_pct >= t.memory_warn:
                alert(AlertSeverity.WARN, "memory", "High Memory Usage",
                      f"Memory at {mem_pct:.1f}%", {"memory_percent": mem_pct})

            if swap_pct >= t.swap_critical:
                alert(AlertSeverity.CRITICAL, "memory", "Heavy Swap Activity",
                      f"Swap at {swap_pct:.1f}%", {"swap_percent": swap_pct})
            elif swap_pct >= t.swap_warn:
                alert(AlertSeverity.WARN, "memory", "Swap Usage Elevated",
                      f"Swap at {swap_pct:.1f}%", {"swap_percent": swap_pct})

            if swap_sout > 100:
                alert(AlertSeverity.WARN, "memory", "Active Swap-Out Detected",
                      f"Swap-out rate {swap_sout:.0f} bytes/s", {"swap_sout_per_sec": swap_sout})

            if oom_new >= 1:
                alert(AlertSeverity.EMERGENCY, "memory", "OOM Kill Detected",
                      f"{oom_new} OOM kill(s) in last cycle", {"oom_kill_count_new": oom_new})

        # --- Disk ---
        disk = snapshots.get("disk")
        if disk and disk.status == "ok" and disk.metrics:
            for mount in disk.metrics.get("mounts", []):
                mp = mount.get("mountpoint", "")
                dpct = mount.get("percent_used", 0)
                ipct = mount.get("inodes_percent", 0)
                if dpct >= t.disk_critical:
                    alert(AlertSeverity.CRITICAL, "disk", f"Disk Space Critical: {mp}",
                          f"Disk {mp} at {dpct:.1f}%", {"mountpoint": mp, "disk_percent": dpct})
                elif dpct >= t.disk_warn:
                    alert(AlertSeverity.WARN, "disk", f"Disk Space Warning: {mp}",
                          f"Disk {mp} at {dpct:.1f}%", {"mountpoint": mp, "disk_percent": dpct})
                if ipct >= t.disk_critical:
                    alert(AlertSeverity.CRITICAL, "disk", f"Inode Exhaustion Critical: {mp}",
                          f"Inodes {mp} at {ipct:.1f}%", {"mountpoint": mp, "inodes_percent": ipct})
                elif ipct >= t.disk_warn:
                    alert(AlertSeverity.WARN, "disk", f"Inode Exhaustion Warning: {mp}",
                          f"Inodes {mp} at {ipct:.1f}%", {"mountpoint": mp, "inodes_percent": ipct})

            for disk_name, io in disk.metrics.get("io", {}).items():
                await_ms = io.get("await_ms", 0)
                if await_ms >= t.disk_await_critical_ms:
                    alert(AlertSeverity.CRITICAL, "disk", f"Critical Disk Latency: {disk_name}",
                          f"Disk {disk_name} await {await_ms:.0f}ms", {"disk": disk_name, "await_ms": await_ms})
                elif await_ms >= t.disk_await_warn_ms:
                    alert(AlertSeverity.WARN, "disk", f"High Disk Latency: {disk_name}",
                          f"Disk {disk_name} await {await_ms:.0f}ms", {"disk": disk_name, "await_ms": await_ms})

        # --- Network ---
        net = snapshots.get("network")
        if net and net.status == "ok" and net.metrics:
            cw = net.metrics.get("tcp_connections", {}).get("close_wait", 0)
            if cw >= t.tcp_close_wait_critical:
                alert(AlertSeverity.CRITICAL, "network", "Severe Connection Leak",
                      f"TCP CLOSE_WAIT: {cw}", {"close_wait": cw})
            elif cw >= t.tcp_close_wait_warn:
                alert(AlertSeverity.WARN, "network", "TCP CLOSE_WAIT Buildup",
                      f"TCP CLOSE_WAIT: {cw}", {"close_wait": cw})

            for iface, stats in net.metrics.get("interfaces", {}).items():
                err_rate = stats.get("error_rate_percent", 0)
                if err_rate >= t.network_error_rate_warn:
                    alert(AlertSeverity.WARN, "network", f"Network Errors: {iface}",
                          f"Error rate {err_rate:.2f}% on {iface}", {"iface": iface, "error_rate": err_rate})

        # --- System Events ---
        se = snapshots.get("system_events")
        if se and se.status == "ok" and se.metrics:
            m = se.metrics
            if m.get("oom_kill_count_new", 0) >= 1:
                alert(AlertSeverity.EMERGENCY, "system_event", "OOM Kill Detected",
                      f"{m['oom_kill_count_new']} OOM kill(s)", {"oom_kill_count_new": m["oom_kill_count_new"]})

            for unit in m.get("failed_systemd_units", []):
                unit_name = unit.get("unit", "unknown")
                alert(AlertSeverity.CRITICAL, "system_event", f"Systemd Unit Failed: {unit_name}",
                      f"Unit {unit_name} failed", {"unit": unit_name, "active": unit.get("active", "")})

            high_levels = {"err", "crit", "alert", "emerg"}
            new_kernel_errors = [e for e in m.get("dmesg_errors", []) if e.get("level") in high_levels]
            if new_kernel_errors:
                sample = new_kernel_errors[0].get("message", "")[:200]
                alert(AlertSeverity.CRITICAL, "system_event", "Kernel Error Detected",
                      f"{len(new_kernel_errors)} kernel error(s): {sample}",
                      {"error_count": len(new_kernel_errors)})

        # --- EC2 ---
        ec2 = snapshots.get("ec2")
        if ec2 and ec2.status == "ok" and ec2.metrics:
            spot = ec2.metrics.get("spot_interruption", {})
            if spot.get("scheduled"):
                alert(AlertSeverity.EMERGENCY, "ec2", "SPOT TERMINATION IN 2 MINUTES",
                      f"Spot interruption notice: action={spot.get('action')} at {spot.get('notice_time')}",
                      spot)

        # --- App Health ---
        ah = snapshots.get("app_health")
        if ah and ah.status == "ok" and ah.metrics:
            for chk in ah.metrics.get("checks", []):
                if not chk.get("healthy"):
                    name = chk.get("name", "unknown")
                    err = chk.get("error", "")
                    # Find original config to check critical_on_failure
                    critical = True
                    for cfg_chk in self.config.app_health_checks:
                        if cfg_chk.name == name:
                            critical = cfg_chk.critical_on_failure
                            break
                    sev = AlertSeverity.CRITICAL if critical else AlertSeverity.WARN
                    alert(sev, "app_health", f"Health Check Failed: {name}",
                          f"Health check '{name}' failed: {err}",
                          {"name": name, "type": chk.get("type"), "error": err})

        return alerts

    def _check_recovery(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        """Return recovery alerts for conditions that resolved."""
        if not self.config.alerts.recovery_notifications:
            return []
        recovery_alerts = []
        current_fps = {a.fingerprint for a in self.evaluate(snapshots)}
        now = time.time()
        for fp, (orig_alert, first_seen, last_sent) in list(self._active.items()):
            if fp not in current_fps:
                rec = Alert(
                    id=str(uuid.uuid4()),
                    severity=AlertSeverity.INFO,
                    category=orig_alert.category,
                    title=f"Recovered: {orig_alert.title}",
                    message=f"Condition resolved after {(now - first_seen):.0f}s",
                    metrics=orig_alert.metrics,
                    instance_id=orig_alert.instance_id,
                    instance_name=orig_alert.instance_name,
                    environment=orig_alert.environment,
                    timestamp=now,
                    fingerprint=_fingerprint(orig_alert.category, f"Recovered: {orig_alert.title}"),
                )
                recovery_alerts.append(rec)
                del self._active[fp]
        return recovery_alerts

    def dispatch(self, alerts: List[Alert]) -> None:
        now = time.time()
        cooldown = self.config.alerts.cooldown_seconds
        escalation_secs = self.config.alerts.escalation_minutes * 60

        to_send: List[Alert] = []

        for alert in alerts:
            fp = alert.fingerprint
            if fp in self._active:
                orig_alert, first_seen, last_sent = self._active[fp]
                # Escalation: WARN that lived too long → upgrade to CRITICAL
                if orig_alert.severity == AlertSeverity.WARN and (now - first_seen) > escalation_secs:
                    escalated = Alert(
                        id=str(uuid.uuid4()),
                        severity=AlertSeverity.CRITICAL,
                        category=alert.category,
                        title=alert.title,
                        message=f"[ESCALATED] {alert.message}",
                        metrics=alert.metrics,
                        instance_id=alert.instance_id,
                        instance_name=alert.instance_name,
                        environment=alert.environment,
                        timestamp=now,
                        fingerprint=fp,
                    )
                    to_send.append(escalated)
                    self._active[fp] = (escalated, first_seen, now)
                    continue
                # Severity upgrade always sends
                if alert.severity.value > orig_alert.severity.value:
                    to_send.append(alert)
                    self._active[fp] = (alert, first_seen, now)
                    continue
                # Cooldown: skip if within window
                if (now - last_sent) < cooldown:
                    continue
                to_send.append(alert)
                self._active[fp] = (alert, first_seen, now)
            else:
                to_send.append(alert)
                self._active[fp] = (alert, now, now)

        # Recovery checks
        # We don't re-evaluate here to avoid double-collect; recovery handled in main loop

        for alert in to_send:
            sent_to = []
            for alerter in self.alerters:
                if not alerter.is_enabled():
                    continue
                try:
                    ok = alerter.send(alert)
                    if ok:
                        sent_to.append(alerter.name)
                except Exception as exc:
                    _log.error("alerter %s error: %s", alerter.name, exc)
            if sent_to:
                _log.info("alert dispatched to %s: [%s] %s", sent_to, alert.severity.name, alert.title)
            else:
                _log.warning("alert not delivered to any channel: [%s] %s", alert.severity.name, alert.title)
