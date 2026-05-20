from __future__ import annotations

import socket
import time
from typing import Dict, Optional, Tuple

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_SKIP_IFACES = {"lo"}
_SKIP_PREFIXES = ("docker", "veth", "br-", "virbr")


def _should_skip_iface(name: str) -> bool:
    if name in _SKIP_IFACES:
        return True
    return any(name.startswith(p) for p in _SKIP_PREFIXES)


def _parse_proc_net_snmp() -> Tuple[int, int, int]:
    """Return (RetransSegs, EstabResets, AttemptFails) from /proc/net/snmp."""
    try:
        with open("/proc/net/snmp", "r") as f:
            content = f.read()
        lines = content.splitlines()
        tcp_keys: Optional[list] = None  # type: ignore[assignment]
        for line in lines:
            if line.startswith("Tcp:"):
                if tcp_keys is None:
                    tcp_keys = line.split()
                else:
                    vals = line.split()
                    def _idx(key: str) -> int:
                        return tcp_keys.index(key) if key in tcp_keys else -1
                    retrans = int(vals[_idx("RetransSegs")]) if _idx("RetransSegs") >= 0 else 0
                    resets = int(vals[_idx("EstabResets")]) if _idx("EstabResets") >= 0 else 0
                    attempts = int(vals[_idx("AttemptFails")]) if _idx("AttemptFails") >= 0 else 0
                    return retrans, resets, attempts
    except Exception:
        pass
    return 0, 0, 0


def _parse_sockstat() -> Dict[str, int]:
    result: Dict[str, int] = {
        "tcp_alloc": 0, "tcp_mem_pages": 0,
        "udp_inuse": 0, "udp_mem_pages": 0,
        "sockets_used": 0,
    }
    try:
        with open("/proc/net/sockstat", "r") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "sockets:":
                    for i, p in enumerate(parts):
                        if p == "used" and i + 1 < len(parts):
                            result["sockets_used"] = int(parts[i + 1])
                elif parts[0] == "TCP:":
                    for i, p in enumerate(parts):
                        if p == "alloc" and i + 1 < len(parts):
                            result["tcp_alloc"] = int(parts[i + 1])
                        elif p == "mem" and i + 1 < len(parts):
                            result["tcp_mem_pages"] = int(parts[i + 1])
                elif parts[0] == "UDP:":
                    for i, p in enumerate(parts):
                        if p == "inuse" and i + 1 < len(parts):
                            result["udp_inuse"] = int(parts[i + 1])
                        elif p == "mem" and i + 1 < len(parts):
                            result["udp_mem_pages"] = int(parts[i + 1])
    except Exception:
        pass
    return result


def _dns_latency(host: str = "169.254.169.253", timeout: float = 2.0) -> float:
    """Return latency in ms, or -1.0 on failure."""
    try:
        start = time.time()
        socket.getaddrinfo(host, None)
        return (time.time() - start) * 1000.0
    except OSError:
        return -1.0
    except Exception:
        return -1.0


class NetworkCollector(BaseCollector):
    name = "network"

    def __init__(self, dns_check_host: str = "169.254.169.253") -> None:
        self._prev_net: Optional[Dict] = None  # type: ignore[type-arg]
        self._prev_snmp: Tuple[int, int, int] = (0, 0, 0)
        self._prev_time: float = 0.0
        self._dns_check_host = dns_check_host

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            now = time.time()
            curr_net = psutil.net_io_counters(pernic=True)
            curr_snmp = _parse_proc_net_snmp()
            elapsed = now - self._prev_time if self._prev_time > 0 else 1.0

            interfaces: Dict[str, Dict] = {}  # type: ignore[type-arg]
            stats = psutil.net_if_stats()
            for iface, counters in curr_net.items():
                if _should_skip_iface(iface):
                    continue
                prev = self._prev_net.get(iface) if self._prev_net else None
                st = stats.get(iface)

                if prev and elapsed > 0:
                    bs = max(0, counters.bytes_sent - prev.bytes_sent) / elapsed
                    br = max(0, counters.bytes_recv - prev.bytes_recv) / elapsed
                    ps = max(0, counters.packets_sent - prev.packets_sent) / elapsed
                    pr = max(0, counters.packets_recv - prev.packets_recv) / elapsed
                    ei = max(0, counters.errin - prev.errin) / elapsed
                    eo = max(0, counters.errout - prev.errout) / elapsed
                    di = max(0, counters.dropin - prev.dropin) / elapsed
                    do_ = max(0, counters.dropout - prev.dropout) / elapsed
                    total_pkts = ps + pr
                    err_rate = ((ei + eo) / total_pkts * 100.0) if total_pkts else 0.0
                    drop_rate = ((di + do_) / total_pkts * 100.0) if total_pkts else 0.0
                else:
                    bs = br = ps = pr = ei = eo = di = do_ = err_rate = drop_rate = 0.0

                interfaces[iface] = {
                    "bytes_sent_per_sec": bs,
                    "bytes_recv_per_sec": br,
                    "packets_sent_per_sec": ps,
                    "packets_recv_per_sec": pr,
                    "errors_in": counters.errin,
                    "errors_out": counters.errout,
                    "drops_in": counters.dropin,
                    "drops_out": counters.dropout,
                    "errors_in_per_sec": ei,
                    "errors_out_per_sec": eo,
                    "drops_in_per_sec": di,
                    "drops_out_per_sec": do_,
                    "error_rate_percent": err_rate,
                    "drop_rate_percent": drop_rate,
                    "is_up": st.isup if st else False,
                    "speed_mbps": st.speed if st else 0,
                    "mtu": st.mtu if st else 0,
                }

            # Full TCP connection state counts
            try:
                conns = psutil.net_connections(kind="tcp")
            except (psutil.AccessDenied, PermissionError):
                conns = []
            tcp_counts: Dict[str, int] = {
                "established": 0, "syn_sent": 0, "syn_recv": 0,
                "fin_wait1": 0, "fin_wait2": 0, "time_wait": 0,
                "close_wait": 0, "closing": 0, "last_ack": 0,
                "listen": 0, "total": 0,
            }
            _state_map = {
                "established": "established",
                "syn_sent": "syn_sent",
                "syn_recv": "syn_recv",
                "fin_wait1": "fin_wait1",
                "fin_wait2": "fin_wait2",
                "time_wait": "time_wait",
                "close_wait": "close_wait",
                "closing": "closing",
                "last_ack": "last_ack",
                "listen": "listen",
            }
            for c in conns:
                status = c.status.lower() if c.status else ""
                tcp_counts["total"] += 1
                key = _state_map.get(status)
                if key:
                    tcp_counts[key] += 1

            # TCP stats — rates
            retrans_rate = 0.0
            resets_rate = 0.0
            attempts_rate = 0.0
            if self._prev_time > 0 and elapsed > 0:
                retrans_rate = max(0, curr_snmp[0] - self._prev_snmp[0]) / elapsed
                resets_rate = max(0, curr_snmp[1] - self._prev_snmp[1]) / elapsed
                if len(curr_snmp) > 2 and len(self._prev_snmp) > 2:
                    attempts_rate = max(0, curr_snmp[2] - self._prev_snmp[2]) / elapsed

            self._prev_net = {k: v for k, v in curr_net.items()}
            self._prev_snmp = curr_snmp
            self._prev_time = now

            dns_latency_ms = _dns_latency(self._dns_check_host)
            dns_healthy = dns_latency_ms >= 0.0

            metrics = {
                "interfaces": interfaces,
                "tcp_connections": tcp_counts,
                "tcp_stats": {
                    "retransmits_per_sec": retrans_rate,
                    "resets_per_sec": resets_rate,
                    "failed_attempts_per_sec": attempts_rate,
                },
                "sockstat": _parse_sockstat(),
                "dns_latency_ms": dns_latency_ms,
                "dns_healthy": dns_healthy,
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("network collect error: %s", exc)
            return MetricSnapshot(
                collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc)
            )
