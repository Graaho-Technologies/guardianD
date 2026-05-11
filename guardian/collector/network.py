from __future__ import annotations

import socket
import time
from typing import Dict, Optional, Tuple

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)


def _parse_proc_net_snmp() -> Tuple[int, int]:
    """Return (RetransSegs, EstabResets) from /proc/net/snmp."""
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
                    retrans_idx = tcp_keys.index("RetransSegs") if "RetransSegs" in tcp_keys else -1
                    resets_idx = tcp_keys.index("EstabResets") if "EstabResets" in tcp_keys else -1
                    retrans = int(vals[retrans_idx]) if retrans_idx >= 0 else 0
                    resets = int(vals[resets_idx]) if resets_idx >= 0 else 0
                    return retrans, resets
    except Exception:
        pass
    return 0, 0


def _dns_latency(host: str = "amazon.com", timeout: float = 2.0) -> float:
    try:
        start = time.time()
        socket.getaddrinfo(host, None, socket.AF_INET)
        return (time.time() - start) * 1000.0
    except Exception:
        return -1.0


class NetworkCollector(BaseCollector):
    name = "network"

    def __init__(self) -> None:
        self._prev_net: Optional[Dict] = None  # type: ignore[type-arg]
        self._prev_snmp: Tuple[int, int] = (0, 0)
        self._prev_time: float = 0.0

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
                if iface == "lo":
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
                    err_rate = ((ei + eo) / total_pkts * 100) if total_pkts else 0.0
                else:
                    bs = br = ps = pr = ei = eo = di = do_ = err_rate = 0.0

                interfaces[iface] = {
                    "bytes_sent_per_sec": bs,
                    "bytes_recv_per_sec": br,
                    "packets_sent_per_sec": ps,
                    "packets_recv_per_sec": pr,
                    "errors_in_per_sec": ei,
                    "errors_out_per_sec": eo,
                    "drops_in_per_sec": di,
                    "drops_out_per_sec": do_,
                    "error_rate_percent": err_rate,
                    "is_up": st.isup if st else False,
                    "speed_mbps": st.speed if st else 0,
                    "mtu": st.mtu if st else 0,
                }

            # TCP connection counts
            conns = psutil.net_connections(kind="tcp")
            tcp_counts: Dict[str, int] = {"established": 0, "time_wait": 0, "close_wait": 0, "listen": 0, "total": 0}
            for c in conns:
                status = c.status.lower() if c.status else ""
                tcp_counts["total"] += 1
                if status == "established":
                    tcp_counts["established"] += 1
                elif status == "time_wait":
                    tcp_counts["time_wait"] += 1
                elif status == "close_wait":
                    tcp_counts["close_wait"] += 1
                elif status == "listen":
                    tcp_counts["listen"] += 1

            # TCP stats
            retrans_rate = 0.0
            resets_rate = 0.0
            if self._prev_time > 0 and elapsed > 0:
                retrans_rate = max(0, curr_snmp[0] - self._prev_snmp[0]) / elapsed
                resets_rate = max(0, curr_snmp[1] - self._prev_snmp[1]) / elapsed

            self._prev_net = {k: v for k, v in curr_net.items()}
            self._prev_snmp = curr_snmp
            self._prev_time = now

            metrics = {
                "interfaces": interfaces,
                "tcp_connections": tcp_counts,
                "tcp_stats": {
                    "retransmits_per_sec": retrans_rate,
                    "resets_per_sec": resets_rate,
                },
                "dns_latency_ms": _dns_latency(),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("network collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
