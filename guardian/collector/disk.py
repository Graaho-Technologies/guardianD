from __future__ import annotations

import time
from typing import Dict, Optional

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_SKIP_FSTYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "devpts", "cgroup", "cgroup2"}


class DiskCollector(BaseCollector):
    name = "disk"

    def __init__(self) -> None:
        self._prev_io: Optional[Dict] = None  # type: ignore[type-arg]
        self._prev_time: float = 0.0

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            mounts = []
            for part in psutil.disk_partitions(all=False):
                if part.fstype in _SKIP_FSTYPES:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    inodes = psutil.disk_usage(part.mountpoint)
                    # inodes via os.statvfs
                    import os
                    st = os.statvfs(part.mountpoint)
                    inodes_total = st.f_files
                    inodes_used = st.f_files - st.f_favail
                    inodes_free = st.f_favail
                    inodes_pct = (inodes_used / inodes_total * 100) if inodes_total else 0.0
                    mounts.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                        "percent_used": usage.percent,
                        "inodes_total": inodes_total,
                        "inodes_used": inodes_used,
                        "inodes_free": inodes_free,
                        "inodes_percent": inodes_pct,
                    })
                except Exception:
                    continue

            now = time.time()
            io_metrics: Dict[str, Dict] = {}  # type: ignore[type-arg]
            try:
                curr_io = psutil.disk_io_counters(perdisk=True)
                if curr_io and self._prev_io and (now - self._prev_time) > 0:
                    elapsed = now - self._prev_time
                    for disk, counters in curr_io.items():
                        prev = self._prev_io.get(disk)
                        if prev is None:
                            continue
                        read_bytes_delta = max(0, counters.read_bytes - prev.read_bytes)
                        write_bytes_delta = max(0, counters.write_bytes - prev.write_bytes)
                        read_count_delta = max(0, counters.read_count - prev.read_count)
                        write_count_delta = max(0, counters.write_count - prev.write_count)
                        read_time_delta = max(0, counters.read_time - prev.read_time)
                        write_time_delta = max(0, counters.write_time - prev.write_time)
                        busy_time_delta = max(0, getattr(counters, "busy_time", 0) - getattr(prev, "busy_time", 0))

                        read_latency = (read_time_delta / read_count_delta) if read_count_delta else 0.0
                        write_latency = (write_time_delta / write_count_delta) if write_count_delta else 0.0
                        total_ops = read_count_delta + write_count_delta
                        total_time = read_time_delta + write_time_delta
                        await_ms = (total_time / total_ops) if total_ops else 0.0
                        busy_pct = (busy_time_delta / (elapsed * 1000) * 100) if elapsed else 0.0

                        io_metrics[disk] = {
                            "read_bytes_per_sec": read_bytes_delta / elapsed,
                            "write_bytes_per_sec": write_bytes_delta / elapsed,
                            "read_ops_per_sec": read_count_delta / elapsed,
                            "write_ops_per_sec": write_count_delta / elapsed,
                            "read_latency_ms": read_latency,
                            "write_latency_ms": write_latency,
                            "await_ms": await_ms,
                            "busy_percent": min(100.0, busy_pct),
                        }
                if curr_io:
                    self._prev_io = dict(curr_io)
            except Exception as exc:
                _log.debug("disk io error: %s", exc)
            self._prev_time = now

            metrics = {"mounts": mounts, "io": io_metrics}
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("disk collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
