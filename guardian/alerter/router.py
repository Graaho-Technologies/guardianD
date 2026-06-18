from __future__ import annotations

import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger
from .ai import AIEnricher
from .base import Alert, AlertSeverity, BaseAlerter, make_fingerprint

_log = get_logger(__name__)

# Per-disk-type latency threshold attribute names
_DISK_AWAIT_ATTRS: Dict[str, Tuple[str, str]] = {
    "nvme":    ("disk_await_nvme_warn_ms",  "disk_await_nvme_critical_ms"),
    "ebs":     ("disk_await_ebs_warn_ms",   "disk_await_ebs_critical_ms"),
    "hdd":     ("disk_await_hdd_warn_ms",   "disk_await_hdd_critical_ms"),
    "ssd":     ("disk_await_ssd_warn_ms",   "disk_await_ssd_critical_ms"),
    "unknown": ("disk_await_ssd_warn_ms",   "disk_await_ssd_critical_ms"),
}


def _fingerprint(category: str, title: str) -> str:
    """Kept for test compatibility; delegates to make_fingerprint."""
    return make_fingerprint(category, title)


def _make_alert(
    severity: AlertSeverity,
    category: str,
    title: str,
    message: str,
    metrics: dict,  # type: ignore[type-arg]
    config: GuardianConfig,
    instance_id: str = "",
    is_recovery: bool = False,
    anomaly_score: float = 0.0,
    forecast_eta_minutes: float = 0.0,
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
        is_recovery=is_recovery,
        anomaly_score=anomaly_score,
        forecast_eta_minutes=forecast_eta_minutes,
    )


class AlertRouter:
    def __init__(self, config: GuardianConfig, alerters: List[BaseAlerter]) -> None:
        self.config = config
        self.alerters = alerters
        self._ai = AIEnricher(config.ai)
        # fingerprint → (alert, first_seen_ts, last_sent_ts)
        self._active: Dict[str, Tuple[Alert, float, float]] = {}
        # fingerprint → consecutive cycles a debounced breach has been true but
        # not yet fired (breach debounce). Cleared once it stops breaching.
        self._breach_streak: Dict[str, int] = {}
        # fingerprint → consecutive cycles an active alert has been resolved
        # (recovery hysteresis). Reset if it re-breaches before clearing.
        self._clear_streak: Dict[str, int] = {}

    def _instance_id(self, snapshots: Dict[str, MetricSnapshot]) -> str:
        ec2 = snapshots.get("ec2")
        if ec2 and ec2.metrics.get("instance_id"):
            return str(ec2.metrics["instance_id"])
        import socket
        return socket.gethostname()

    def evaluate(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        alerts: List[Alert] = []
        instance_id = self._instance_id(snapshots)

        def _a(sev: AlertSeverity, cat: str, title: str, msg: str, metrics: dict) -> None:  # type: ignore[type-arg]
            alerts.append(_make_alert(sev, cat, title, msg, metrics, self.config, instance_id))

        t = self.config.thresholds

        # --- CPU ---
        cpu = snapshots.get("cpu")
        if cpu and cpu.status != "error" and cpu.metrics:
            m = cpu.metrics
            pct = m.get("percent_total", 0.0)
            steal = m.get("times_steal", 0.0)
            iowait = m.get("times_iowait", 0.0)
            load_norm = m.get("load_avg_normalized_1m", 0.0)

            if pct >= t.cpu_critical:
                _a(AlertSeverity.CRITICAL, "cpu", "Critical CPU Usage",
                   f"CPU usage at {pct:.1f}%", {"cpu_percent": pct})
            elif pct >= t.cpu_warn:
                _a(AlertSeverity.WARN, "cpu", "High CPU Usage",
                   f"CPU usage at {pct:.1f}%", {"cpu_percent": pct})

            if steal >= t.cpu_steal_critical:
                _a(AlertSeverity.CRITICAL, "cpu", "Severe EC2 CPU Steal — Noisy Neighbor",
                   f"CPU steal at {steal:.1f}%", {"cpu_steal": steal})
            elif steal >= t.cpu_steal_warn:
                _a(AlertSeverity.WARN, "cpu", "EC2 CPU Steal Detected",
                   f"CPU steal at {steal:.1f}%", {"cpu_steal": steal})

            if iowait >= t.cpu_iowait_critical:
                _a(AlertSeverity.CRITICAL, "cpu", "Critical I/O Wait — Disk Bottleneck",
                   f"iowait at {iowait:.1f}%", {"iowait": iowait})
            elif iowait >= t.cpu_iowait_warn:
                _a(AlertSeverity.WARN, "cpu", "High I/O Wait",
                   f"iowait at {iowait:.1f}%", {"iowait": iowait})

            if load_norm >= t.load_avg_critical_multiplier:
                _a(AlertSeverity.CRITICAL, "cpu", "Critical System Load",
                   f"Normalized load {load_norm:.2f}", {"load_normalized": load_norm})
            elif load_norm >= t.load_avg_warn_multiplier:
                _a(AlertSeverity.WARN, "cpu", "High System Load",
                   f"Normalized load {load_norm:.2f}", {"load_normalized": load_norm})

        # --- Memory ---
        mem = snapshots.get("memory")
        if mem and mem.status != "error" and mem.metrics:
            m = mem.metrics
            mem_pct = m.get("percent_used", 0.0)
            swap_pct = m.get("swap_percent", 0.0)
            swap_sout = m.get("swap_sout_per_sec", 0.0)
            dirty_ratio = m.get("dirty_ratio_percent", 0.0)
            fd_pct = m.get("fd_percent_used", 0.0)
            oom_new = m.get("oom_kill_count_new", 0)

            if mem_pct >= t.memory_critical:
                _a(AlertSeverity.CRITICAL, "memory", "Critical Memory Usage",
                   f"Memory at {mem_pct:.1f}%", {"memory_percent": mem_pct})
            elif mem_pct >= t.memory_warn:
                _a(AlertSeverity.WARN, "memory", "High Memory Usage",
                   f"Memory at {mem_pct:.1f}%", {"memory_percent": mem_pct})

            if swap_pct >= t.swap_critical:
                _a(AlertSeverity.CRITICAL, "memory",
                   "Heavy Swap Activity — Memory Exhaustion Risk",
                   f"Swap at {swap_pct:.1f}%", {"swap_percent": swap_pct})
            elif swap_pct >= t.swap_warn:
                _a(AlertSeverity.WARN, "memory", "Swap Usage Elevated",
                   f"Swap at {swap_pct:.1f}%", {"swap_percent": swap_pct})

            if swap_sout >= t.swap_sout_critical:
                _a(AlertSeverity.CRITICAL, "memory",
                   "Severe Swap-Out Rate — Active Memory Pressure",
                   f"Swap-out rate {swap_sout:.1f} pages/s", {"swap_sout_per_sec": swap_sout})
            elif swap_sout >= t.swap_sout_warn:
                _a(AlertSeverity.WARN, "memory", "Active Swap-Out Detected",
                   f"Swap-out rate {swap_sout:.1f} pages/s", {"swap_sout_per_sec": swap_sout})

            if dirty_ratio >= t.dirty_ratio_critical:
                _a(AlertSeverity.CRITICAL, "memory",
                   "Excessive Dirty Pages — I/O Stall Imminent",
                   f"Dirty pages at {dirty_ratio:.1f}% of RAM", {"dirty_ratio_percent": dirty_ratio})
            elif dirty_ratio >= t.dirty_ratio_warn:
                _a(AlertSeverity.WARN, "memory", "High Dirty Page Ratio",
                   f"Dirty pages at {dirty_ratio:.1f}% of RAM", {"dirty_ratio_percent": dirty_ratio})

            if fd_pct >= t.fd_exhaustion_critical:
                _a(AlertSeverity.CRITICAL, "memory",
                   "File Descriptor Exhaustion — Services Will Crash",
                   f"FD usage at {fd_pct:.1f}%", {"fd_percent_used": fd_pct})
            elif fd_pct >= t.fd_exhaustion_warn:
                _a(AlertSeverity.WARN, "memory", "File Descriptor Exhaustion Warning",
                   f"FD usage at {fd_pct:.1f}%", {"fd_percent_used": fd_pct})

            if oom_new >= 1:
                _a(AlertSeverity.EMERGENCY, "memory",
                   "OOM Kill Detected — Process Killed by Kernel",
                   f"{oom_new} OOM kill(s) in last cycle", {"oom_kill_count_new": oom_new})

        # --- Disk ---
        disk = snapshots.get("disk")
        if disk and disk.status != "error" and disk.metrics:
            for mount in disk.metrics.get("mounts", []):
                mp = mount.get("mountpoint", "")
                dpct = mount.get("percent_used", 0.0)
                ipct = mount.get("inodes_percent", 0.0)

                if dpct >= t.disk_critical:
                    _a(AlertSeverity.CRITICAL, "disk", f"Disk Space Critical: {mp}",
                       f"Disk {mp} at {dpct:.1f}%", {"mountpoint": mp, "disk_percent": dpct})
                elif dpct >= t.disk_warn:
                    _a(AlertSeverity.WARN, "disk", f"Disk Space Warning: {mp}",
                       f"Disk {mp} at {dpct:.1f}%", {"mountpoint": mp, "disk_percent": dpct})

                if ipct >= t.inode_critical:
                    _a(AlertSeverity.CRITICAL, "disk", f"Inode Exhaustion Critical: {mp}",
                       f"Inodes {mp} at {ipct:.1f}%", {"mountpoint": mp, "inodes_percent": ipct})
                elif ipct >= t.inode_warn:
                    _a(AlertSeverity.WARN, "disk", f"Inode Exhaustion Warning: {mp}",
                       f"Inodes {mp} at {ipct:.1f}%", {"mountpoint": mp, "inodes_percent": ipct})

            for disk_name, io in disk.metrics.get("io", {}).items():
                await_ms = io.get("await_ms", 0.0)
                # await is total_time/total_ops — meaningless on a near-idle disk
                # where one slow I/O dominates a tiny denominator. Require enough
                # ops in the interval before treating latency as alertable.
                if io.get("total_ops", 0.0) < t.disk_await_min_ops:
                    continue
                disk_type = io.get("disk_type", "unknown")
                warn_attr, crit_attr = _DISK_AWAIT_ATTRS.get(disk_type, _DISK_AWAIT_ATTRS["unknown"])
                warn_val = getattr(t, warn_attr, 10.0)
                crit_val = getattr(t, crit_attr, 50.0)

                if await_ms >= crit_val:
                    _a(AlertSeverity.CRITICAL, "disk",
                       f"Critical Disk Latency: {disk_name} ({disk_type})",
                       f"Disk {disk_name} await {await_ms:.0f}ms",
                       {"disk": disk_name, "await_ms": await_ms, "disk_type": disk_type})
                elif await_ms >= warn_val:
                    _a(AlertSeverity.WARN, "disk",
                       f"High Disk Latency: {disk_name} ({disk_type})",
                       f"Disk {disk_name} await {await_ms:.0f}ms",
                       {"disk": disk_name, "await_ms": await_ms, "disk_type": disk_type})

        # --- Network ---
        net = snapshots.get("network")
        if net and net.status != "error" and net.metrics:
            tcp = net.metrics.get("tcp_connections", {})
            cw = tcp.get("close_wait", 0)
            syn_recv = tcp.get("syn_recv", 0)

            if cw >= t.tcp_close_wait_critical:
                _a(AlertSeverity.CRITICAL, "network",
                   "Severe TCP CLOSE_WAIT — Application Connection Leak",
                   f"TCP CLOSE_WAIT: {cw}", {"close_wait": cw})
            elif cw >= t.tcp_close_wait_warn:
                _a(AlertSeverity.WARN, "network", "TCP CLOSE_WAIT Buildup Detected",
                   f"TCP CLOSE_WAIT: {cw}", {"close_wait": cw})

            if syn_recv >= t.tcp_syn_recv_warn:
                _a(AlertSeverity.WARN, "network",
                   "High SYN_RECV Count — Possible SYN Flood",
                   f"TCP SYN_RECV: {syn_recv}", {"syn_recv": syn_recv})

            for iface, stats in net.metrics.get("interfaces", {}).items():
                err_rate = stats.get("error_rate_percent", 0.0)
                drop_rate = stats.get("drop_rate_percent", 0.0)

                # error_rate/drop_rate are a ratio of a tiny packet count on an
                # idle interface — one stray error becomes a huge %. Require
                # meaningful traffic before treating the rate as alertable.
                total_pps = (stats.get("packets_sent_per_sec", 0.0)
                             + stats.get("packets_recv_per_sec", 0.0))
                if total_pps < t.network_min_pps:
                    continue

                if err_rate >= t.network_error_rate_critical:
                    _a(AlertSeverity.CRITICAL, "network",
                       f"Critical Network Error Rate: {iface}",
                       f"Error rate {err_rate:.2f}% on {iface}",
                       {"iface": iface, "error_rate_percent": err_rate})
                elif err_rate >= t.network_error_rate_warn:
                    _a(AlertSeverity.WARN, "network", f"Network Errors: {iface}",
                       f"Error rate {err_rate:.2f}% on {iface}",
                       {"iface": iface, "error_rate_percent": err_rate})

                if drop_rate >= t.network_drop_rate_critical:
                    _a(AlertSeverity.CRITICAL, "network",
                       f"Critical Network Drop Rate: {iface}",
                       f"Drop rate {drop_rate:.2f}% on {iface}",
                       {"iface": iface, "drop_rate_percent": drop_rate})
                elif drop_rate >= t.network_drop_rate_warn:
                    _a(AlertSeverity.WARN, "network", f"Network Packet Drops: {iface}",
                       f"Drop rate {drop_rate:.2f}% on {iface}",
                       {"iface": iface, "drop_rate_percent": drop_rate})

            if not net.metrics.get("dns_healthy", True):
                _a(AlertSeverity.CRITICAL, "network", "DNS Resolution Failing",
                   f"DNS latency: {net.metrics.get('dns_latency_ms', -1):.1f}ms",
                   {"dns_latency_ms": net.metrics.get("dns_latency_ms", -1.0)})

        # --- Process ---
        proc = snapshots.get("process")
        if proc and proc.status != "error" and proc.metrics:
            m = proc.metrics
            zombies = m.get("zombie", 0)
            dsleep = m.get("disk_sleep_procs", [])
            dsleep_count = len(dsleep) if isinstance(dsleep, list) else m.get("disk_sleep", 0)

            if zombies >= t.zombie_critical:
                _a(AlertSeverity.CRITICAL, "process", "Zombie Process Accumulation",
                   f"{zombies} zombie processes", {"zombie_count": zombies})
            elif zombies >= t.zombie_warn:
                _a(AlertSeverity.WARN, "process", "Zombie Processes Detected",
                   f"{zombies} zombie processes", {"zombie_count": zombies})

            # Note: keep the title stable (count goes in the message/metrics) so
            # the fingerprint doesn't change every cycle and defeat dedup/debounce.
            if dsleep_count >= t.disk_sleep_critical:
                _a(AlertSeverity.CRITICAL, "process",
                   "Processes Stuck in Disk-Sleep (D state)",
                   f"{dsleep_count} process(es) in uninterruptible sleep (D state)",
                   {"disk_sleep_count": dsleep_count})
            elif dsleep_count >= t.disk_sleep_warn:
                _a(AlertSeverity.WARN, "process",
                   "Processes in Disk-Sleep (D state)",
                   f"{dsleep_count} process(es) in uninterruptible sleep (D state)",
                   {"disk_sleep_count": dsleep_count})

        # --- EC2 ---
        ec2 = snapshots.get("ec2")
        if ec2 and ec2.status != "error" and ec2.metrics:
            if ec2.metrics.get("is_ec2"):
                spot = ec2.metrics.get("spot_interruption", {})
                if spot.get("scheduled"):
                    _a(AlertSeverity.EMERGENCY, "ec2",
                       "SPOT INSTANCE TERMINATION NOTICE — 2 MINUTES",
                       f"Spot interruption: action={spot.get('action')} at {spot.get('notice_time')}",
                       spot)

        # --- System Events ---
        se = snapshots.get("system_events")
        if se and se.status != "error" and se.metrics:
            m = se.metrics

            if m.get("oom_kill_count_new", 0) >= 1:
                _a(AlertSeverity.EMERGENCY, "system_event", "OOM Kill Detected",
                   f"{m['oom_kill_count_new']} OOM kill(s)", {"oom_kill_count_new": m["oom_kill_count_new"]})

            for unit in m.get("failed_systemd_units", []):
                unit_name = unit.get("unit", "unknown")
                fp = _fingerprint("system_event", f"Systemd Unit Failed: {unit_name}")
                a = _make_alert(
                    AlertSeverity.CRITICAL, "system_event",
                    f"Systemd Unit Failed: {unit_name}",
                    f"Unit {unit_name} failed",
                    {"unit": unit_name, "active": unit.get("active", "")},
                    self.config, instance_id,
                )
                alerts.append(a)

            high_levels = {"crit", "alert", "emerg"}
            new_kernel_errors = [
                e for e in m.get("dmesg_errors_new", [])
                if e.get("level") in high_levels
            ]
            if new_kernel_errors:
                sample = new_kernel_errors[0].get("message", "")[:40]
                title = "Kernel Critical Event Detected"
                _a(AlertSeverity.CRITICAL, "system_event", title,
                   f"{len(new_kernel_errors)} kernel critical event(s): {sample}",
                   {"error_count": len(new_kernel_errors), "sample": sample})

            # PSI — embedded in system_events
            if m.get("psi_available"):
                psi = m.get("psi", {})
                cpu_psi = psi.get("cpu", {})
                mem_psi = psi.get("memory", {})
                io_psi = psi.get("io", {})

                cpu_some = cpu_psi.get("some_avg10", -1.0)
                if cpu_some >= 0:
                    if cpu_some >= t.psi_cpu_some_critical:
                        _a(AlertSeverity.CRITICAL, "psi", "Severe CPU Pressure (PSI)",
                           f"CPU PSI some avg10={cpu_some:.1f}%", {"psi_cpu_some_avg10": cpu_some})
                    elif cpu_some >= t.psi_cpu_some_warn:
                        _a(AlertSeverity.WARN, "psi", "CPU Pressure Detected (PSI)",
                           f"CPU PSI some avg10={cpu_some:.1f}%", {"psi_cpu_some_avg10": cpu_some})

                mem_some = mem_psi.get("some_avg10", -1.0)
                mem_full = mem_psi.get("full_avg10", -1.0)
                if mem_full >= 0 and mem_full >= t.psi_memory_full_critical:
                    _a(AlertSeverity.CRITICAL, "psi",
                       "Memory Pressure Stall — All Tasks Halted",
                       f"Memory PSI full avg10={mem_full:.1f}%", {"psi_memory_full_avg10": mem_full})
                elif mem_some >= 0:
                    if mem_some >= t.psi_memory_some_critical:
                        _a(AlertSeverity.CRITICAL, "psi", "Severe Memory Pressure (PSI)",
                           f"Memory PSI some avg10={mem_some:.1f}%", {"psi_memory_some_avg10": mem_some})
                    elif mem_some >= t.psi_memory_some_warn:
                        _a(AlertSeverity.WARN, "psi", "Memory Pressure Detected (PSI)",
                           f"Memory PSI some avg10={mem_some:.1f}%", {"psi_memory_some_avg10": mem_some})

                io_some = io_psi.get("some_avg10", -1.0)
                io_full = io_psi.get("full_avg10", -1.0)
                if io_full >= 0 and io_full >= t.psi_io_full_critical:
                    _a(AlertSeverity.CRITICAL, "psi",
                       "I/O Pressure Stall — All Tasks Halted",
                       f"I/O PSI full avg10={io_full:.1f}%", {"psi_io_full_avg10": io_full})
                elif io_some >= 0:
                    if io_some >= t.psi_io_some_critical:
                        _a(AlertSeverity.CRITICAL, "psi", "Severe I/O Pressure (PSI)",
                           f"I/O PSI some avg10={io_some:.1f}%", {"psi_io_some_avg10": io_some})
                    elif io_some >= t.psi_io_some_warn:
                        _a(AlertSeverity.WARN, "psi", "I/O Pressure Detected (PSI)",
                           f"I/O PSI some avg10={io_some:.1f}%", {"psi_io_some_avg10": io_some})

        # --- App Health ---
        ah = snapshots.get("app_health")
        if ah and ah.status != "error" and ah.metrics:
            for chk in ah.metrics.get("checks", []):
                if not chk.get("healthy"):
                    name = chk.get("name", "unknown")
                    err = chk.get("error", "")
                    critical = True
                    failure_threshold = 2
                    for cfg_chk in self.config.app_health_checks:
                        if cfg_chk.name == name:
                            critical = cfg_chk.critical_on_failure
                            failure_threshold = cfg_chk.failure_threshold
                            break
                    # Suppress single transient failures (deploy/restart/GC pause);
                    # only alert once the check has failed N consecutive probes.
                    if chk.get("consecutive_failures", 1) < failure_threshold:
                        continue
                    sev = AlertSeverity.CRITICAL if critical else AlertSeverity.WARN
                    title = f"Health Check Failed: {name}"
                    fp = _fingerprint("app_health", title)
                    a = _make_alert(
                        sev, "app_health", title,
                        f"Health check '{name}' failed: {err}",
                        {"name": name, "type": chk.get("type"), "error": err},
                        self.config, instance_id,
                    )
                    a.fingerprint = fp
                    alerts.append(a)

        return alerts

    def evaluate_intelligence(self, intel_alerts: List[Alert]) -> List[Alert]:
        """Pass-through; dedup handled by dispatch."""
        return intel_alerts

    def _check_recovery(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        if not self.config.alerts.recovery_notifications:
            return []
        current_fps = {a.fingerprint for a in self.evaluate(snapshots)}
        clear_cycles = max(1, self.config.alerts.recovery_clear_cycles)
        recoveries: List[Alert] = []
        now = time.time()
        for fp in list(self._active):
            if fp in current_fps:
                # Still breaching — abandon any in-progress clear streak so a
                # single dip doesn't recover a still-active condition.
                self._clear_streak.pop(fp, None)
                continue
            # Resolved this cycle. Require it to stay clear for N cycles
            # (hysteresis) before emitting a recovery, so a metric oscillating
            # across the threshold doesn't produce a fire/recover storm.
            streak = self._clear_streak.get(fp, 0) + 1
            self._clear_streak[fp] = streak
            if streak < clear_cycles:
                continue
            self._clear_streak.pop(fp, None)
            orig, first_seen, _ = self._active.pop(fp)
            recoveries.append(Alert(
                id=str(uuid.uuid4()),
                severity=AlertSeverity.INFO,
                category=orig.category,
                title=f"Recovered: {orig.title}",
                message=f"Condition resolved. Was: {orig.message}",
                metrics=orig.metrics,
                instance_id=orig.instance_id,
                instance_name=orig.instance_name,
                environment=orig.environment,
                timestamp=now,
                fingerprint=make_fingerprint(orig.category, f"recovery:{orig.title}"),
                is_recovery=True,
            ))
        return recoveries

    def dispatch(self, alerts: List[Alert]) -> List[Alert]:
        now = time.time()
        cooldown = self.config.alerts.cooldown_seconds
        escalation_secs = self.config.alerts.escalation_minutes * 60
        breach_cycles = max(1, self.config.alerts.breach_cycles_to_alert)
        to_send: List[Alert] = []

        # Breach debounce applies only to standard threshold alerts. Intelligence
        # alerts (velocity/anomaly/forecast) are inherently single-cycle events,
        # and EMERGENCY alerts (OOM kill, spot termination) must fire instantly —
        # both bypass the debounce.
        def _debounced(a: Alert) -> bool:
            return (not a.is_recovery
                    and a.category != "intelligence"
                    and a.severity != AlertSeverity.EMERGENCY)

        # Reset streaks for fingerprints that stopped breaching this cycle.
        current_breach_fps = {a.fingerprint for a in alerts if _debounced(a)}
        for fp in list(self._breach_streak):
            if fp not in current_breach_fps:
                del self._breach_streak[fp]

        for alert in alerts:
            if alert.is_recovery:
                to_send.append(alert)
                continue
            if _debounced(alert):
                streak = self._breach_streak.get(alert.fingerprint, 0) + 1
                self._breach_streak[alert.fingerprint] = streak
                # Hold a not-yet-sustained breach unless it's already active
                # (already fired — let cooldown/escalation handle re-sends).
                if streak < breach_cycles and alert.fingerprint not in self._active:
                    continue
            fp = alert.fingerprint
            if fp in self._active:
                orig_alert, first_seen, last_sent = self._active[fp]
                # Escalation takes priority over cooldown
                if (orig_alert.severity == AlertSeverity.WARN and
                        (now - first_seen) > escalation_secs):
                    alert = Alert(
                        id=str(uuid.uuid4()),
                        severity=AlertSeverity.CRITICAL,
                        category=alert.category,
                        title=f"[ESCALATED] {alert.title}",
                        message=alert.message,
                        metrics=alert.metrics,
                        instance_id=alert.instance_id,
                        instance_name=alert.instance_name,
                        environment=alert.environment,
                        timestamp=now,
                        fingerprint=fp,
                        is_recovery=alert.is_recovery,
                    )
                    self._active[fp] = (alert, first_seen, now)
                    to_send.append(alert)
                    continue
                # Cooldown: skip same-or-lower severity within window
                if (now - last_sent) < cooldown:
                    if alert.severity.value <= orig_alert.severity.value:
                        continue
                self._active[fp] = (alert, first_seen, now)
            else:
                self._active[fp] = (alert, now, now)
            to_send.append(alert)

        if not to_send:
            return []

        max_per_dispatch = self.config.alerts.max_alerts_per_dispatch
        batch = to_send[:max_per_dispatch]
        # Enrich once per alert (shared across all channels) before sending.
        # No-op unless AI is enabled; never raises.
        try:
            self._ai.enrich_batch(batch)
        except Exception as exc:
            _log.error("AI enrich_batch error: %s", exc)
        if self.config.alerts.group_alerts and len(batch) > 1:
            self._send_grouped(batch)
        else:
            for alert in batch:
                self._send_single(alert)
        return batch

    def _send_single(self, alert: Alert) -> None:
        with ThreadPoolExecutor(max_workers=max(1, len(self.alerters))) as ex:
            for alerter in self.alerters:
                if alerter.is_enabled():
                    ex.submit(self._safe_send, alerter, alert)

    def _send_grouped(self, alerts: List[Alert]) -> None:
        for alert in alerts:
            self._send_single(alert)

    def _safe_send(self, alerter: BaseAlerter, alert: Alert) -> bool:
        try:
            ok = alerter.send(alert)
            if ok:
                _log.info("alert dispatched via %s: [%s] %s", alerter.name, alert.severity.name, alert.title)
            return ok
        except Exception as exc:
            _log.error("alerter %s error: %s", alerter.name, exc)
            return False
