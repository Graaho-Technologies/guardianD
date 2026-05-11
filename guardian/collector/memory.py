from __future__ import annotations

import time
from typing import Optional

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


class MemoryCollector(BaseCollector):
    name = "memory"

    def __init__(self) -> None:
        self._prev_swap_sin: int = 0
        self._prev_swap_sout: int = 0
        self._prev_time: float = 0.0

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
            self._prev_time = now

            metrics = {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": vm.used,
                "free_bytes": vm.free,
                "percent_used": vm.percent,
                "cached_bytes": getattr(vm, "cached", 0),
                "buffers_bytes": getattr(vm, "buffers", 0),
                "shared_bytes": getattr(vm, "shared", 0),
                "swap_total_bytes": swap.total,
                "swap_used_bytes": swap.used,
                "swap_free_bytes": swap.free,
                "swap_percent": swap.percent,
                "swap_sin_per_sec": sin_rate,
                "swap_sout_per_sec": sout_rate,
                "oom_kills_since_boot": _parse_oom_kills(),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("memory collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
