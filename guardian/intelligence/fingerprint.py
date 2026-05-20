from __future__ import annotations

from typing import Dict, List

from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger

_log = get_logger(__name__)


class BottleneckFingerprinter:
    """
    Correlates metric patterns across collectors to diagnose root cause.
    Returns list of findings that enrich alert messages.
    Does NOT produce Alerts — enriches existing ones.
    """

    def __init__(self, config: GuardianConfig) -> None:
        self.config = config

    def analyze(self, snapshots: Dict[str, MetricSnapshot]) -> List[Dict]:
        """
        Returns: [{"pattern": str, "diagnosis": str, "confidence": float, "evidence": dict}]
        """
        findings: List[Dict] = []
        t = self.config.thresholds

        def _m(name: str) -> Dict:
            snap = snapshots.get(name)
            if snap and snap.status != "error" and snap.metrics:
                return snap.metrics
            return {}

        cpu_m = _m("cpu")
        mem_m = _m("memory")
        disk_m = _m("disk")
        net_m = _m("network")
        se_m = _m("system_events")

        cpu_pct = float(cpu_m.get("percent_total", 0.0))
        iowait = float(cpu_m.get("times_iowait", 0.0))
        steal = float(cpu_m.get("times_steal", 0.0))
        load_norm = float(cpu_m.get("load_avg_normalized_1m", 0.0))
        mem_pct = float(mem_m.get("percent_used", 0.0))
        swap_sout = float(mem_m.get("swap_sout_per_sec", 0.0))
        fd_pct = float(mem_m.get("fd_percent_used", 0.0))
        tcp_conns = net_m.get("tcp_connections", {})
        close_wait = int(tcp_conns.get("close_wait", 0))
        psi = se_m.get("psi", {})
        mem_full_avg10 = float(psi.get("memory", {}).get("full_avg10", -1.0))

        # cpu_bound: high CPU, low iowait, elevated load
        if cpu_pct > 80 and iowait < 20 and load_norm > 1.5:
            findings.append({
                "pattern": "cpu_bound",
                "diagnosis": "CPU-bound workload. High CPU utilization with low I/O wait.",
                "confidence": min(1.0, (cpu_pct - 80.0) / 20.0),
                "evidence": {
                    "cpu_percent": cpu_pct,
                    "iowait": iowait,
                    "load_normalized": load_norm,
                },
            })

        # disk_io_bottleneck: high iowait AND at least one slow disk
        any_disk_slow = any(
            io.get("await_ms", 0.0) > t.disk_await_ssd_warn_ms
            for io in disk_m.get("io", {}).values()
        )
        if iowait > 40 and any_disk_slow:
            findings.append({
                "pattern": "disk_io_bottleneck",
                "diagnosis": f"I/O bottleneck. CPU spending {iowait:.1f}% waiting for disk.",
                "confidence": min(1.0, (iowait - 40.0) / 30.0),
                "evidence": {"iowait": iowait},
            })

        # memory_pressure: high RAM usage AND active swapping
        if mem_pct > 85 and swap_sout > t.swap_sout_warn:
            findings.append({
                "pattern": "memory_pressure",
                "diagnosis": "Memory pressure. Active swapping — OOM risk elevated.",
                "confidence": min(1.0, (mem_pct - 85.0) / 15.0),
                "evidence": {"memory_percent": mem_pct, "swap_sout_per_sec": swap_sout},
            })

        # ec2_noisy_neighbor: steal high, CPU usage low
        if steal > t.cpu_steal_warn and cpu_pct < 60:
            findings.append({
                "pattern": "ec2_noisy_neighbor",
                "diagnosis": (
                    f"EC2 noisy neighbor. Steal {steal:.1f}% with low app CPU "
                    "— consider AZ migration."
                ),
                "confidence": min(1.0, steal / max(t.cpu_steal_critical, 1.0)),
                "evidence": {"cpu_steal": steal, "cpu_percent": cpu_pct},
            })

        # connection_leak: CLOSE_WAIT above warn
        if close_wait > t.tcp_close_wait_warn:
            findings.append({
                "pattern": "connection_leak",
                "diagnosis": (
                    "TCP connection leak — app not closing connections. "
                    "Check pool config."
                ),
                "confidence": min(
                    1.0, close_wait / max(t.tcp_close_wait_critical, 1.0)
                ),
                "evidence": {"close_wait": close_wait},
            })

        # fd_starvation: FD usage above warn
        if fd_pct > t.fd_exhaustion_warn:
            findings.append({
                "pattern": "fd_starvation",
                "diagnosis": (
                    "File descriptor starvation risk. "
                    "Services will crash at FD limit."
                ),
                "confidence": min(
                    1.0,
                    (fd_pct - t.fd_exhaustion_warn) / max(
                        100.0 - t.fd_exhaustion_warn, 1.0
                    ),
                ),
                "evidence": {"fd_percent_used": fd_pct},
            })

        # psi_memory_stall: kernel-confirmed memory stall
        if mem_full_avg10 >= 0 and mem_full_avg10 > t.psi_memory_full_warn:
            findings.append({
                "pattern": "psi_memory_stall",
                "diagnosis": (
                    "Kernel-confirmed memory stall. "
                    "All tasks halted waiting for memory."
                ),
                "confidence": min(
                    1.0, mem_full_avg10 / max(t.psi_memory_full_critical, 1.0)
                ),
                "evidence": {"psi_memory_full_avg10": mem_full_avg10},
            })

        # Sort by confidence descending
        findings.sort(key=lambda f: f["confidence"], reverse=True)
        return findings
