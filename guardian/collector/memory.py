from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)


def _parse_oom_kills() -> int:
    try:
        with open("/proc/vmstat", "r") as f:
            for line in f:
                if line.startswith("oom_kill "):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0


def _parse_meminfo() -> Dict[str, int]:
    result: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    try:
                        result[key] = int(parts[1])
                    except ValueError:
                        pass
    except Exception:
        pass
    return result


def _parse_file_nr() -> Tuple[int, int]:
    """Return (allocated, maximum) from /proc/sys/fs/file-nr."""
    try:
        with open("/proc/sys/fs/file-nr", "r") as f:
            parts = f.read().split()
            return int(parts[0]), int(parts[2])
    except Exception:
        return 0, 1


class MemoryCollector(BaseCollector):
    name = "memory"

    def __init__(self) -> None:
        self._prev_swap_sin: int = 0
        self._prev_swap_sout: int = 0
        self._prev_time: float = 0.0
        self._prev_oom_kills: int = 0

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()

            now = time.time()
            sin_rate = 0.0
            sout_rate = 0.0
            if self._prev_time > 0 and (now - self._prev_time) > 0:
                elapsed = now - self._prev_time
                sin_rate = max(0.0, (swap.sin - self._prev_swap_sin) / elapsed)
                sout_rate = max(0.0, (swap.sout - self._prev_swap_sout) / elapsed)
            self._prev_swap_sin = swap.sin
            self._prev_swap_sout = swap.sout

            # /proc/meminfo extras
            meminfo = _parse_meminfo()
            dirty_kb = meminfo.get("Dirty", 0)
            writeback_kb = meminfo.get("Writeback", 0)
            dirty_bytes = dirty_kb * 1024
            writeback_bytes = writeback_kb * 1024
            dirty_ratio = (dirty_bytes / vm.total * 100.0) if vm.total else 0.0

            hugepages_total = meminfo.get("HugePages_Total", 0)
            hugepages_free = meminfo.get("HugePages_Free", 0)
            hugepages_size_kb = meminfo.get("Hugepagesize", 0)
            hugepages_used = hugepages_total - hugepages_free
            hugepages_pct = (hugepages_used / hugepages_total * 100.0) if hugepages_total else 0.0

            # /proc/sys/fs/file-nr
            fd_allocated, fd_max = _parse_file_nr()
            fd_pct = (fd_allocated / fd_max * 100.0) if fd_max else 0.0

            # OOM kill delta
            oom_since_boot = _parse_oom_kills()
            if self._prev_time == 0:
                oom_count_new = 0
                self._prev_oom_kills = oom_since_boot
            else:
                oom_count_new = max(0, oom_since_boot - self._prev_oom_kills)
                self._prev_oom_kills = oom_since_boot

            self._prev_time = now

            metrics = {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": vm.used,
                "free_bytes": vm.free,
                "cached_bytes": getattr(vm, "cached", 0),
                "buffers_bytes": getattr(vm, "buffers", 0),
                "shared_bytes": getattr(vm, "shared", 0),
                "percent_used": vm.percent,
                "swap_total_bytes": swap.total,
                "swap_used_bytes": swap.used,
                "swap_free_bytes": swap.free,
                "swap_percent": swap.percent,
                "swap_sin_per_sec": sin_rate,
                "swap_sout_per_sec": sout_rate,
                "oom_kills_since_boot": oom_since_boot,
                "oom_kill_count_new": oom_count_new,
                "dirty_bytes": dirty_bytes,
                "dirty_ratio_percent": dirty_ratio,
                "writeback_bytes": writeback_bytes,
                "hugepages_total": hugepages_total,
                "hugepages_free": hugepages_free,
                "hugepages_size_kb": hugepages_size_kb,
                "hugepages_percent_used": hugepages_pct,
                "fd_allocated": fd_allocated,
                "fd_max": fd_max,
                "fd_percent_used": fd_pct,
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("memory collect error: %s", exc)
            return MetricSnapshot(
                collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc)
            )
