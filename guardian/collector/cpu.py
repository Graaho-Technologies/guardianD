from __future__ import annotations

import time
from typing import Optional

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)


class CPUCollector(BaseCollector):
    name = "cpu"

    def __init__(self) -> None:
        self._prev_stats: Optional[psutil._common.scpustats] = None  # type: ignore[name-defined]
        self._prev_time: float = 0.0

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            percent_total = psutil.cpu_percent(interval=None)
            percent_per_core = psutil.cpu_percent(interval=None, percpu=True)
            count_logical = psutil.cpu_count(logical=True) or 1
            count_physical = psutil.cpu_count(logical=False) or 1
            load_1, load_5, load_15 = psutil.getloadavg()
            cpu_times = psutil.cpu_times_percent(interval=None)
            freq = psutil.cpu_freq()

            # Delta stats
            ctx_switches_per_sec = 0.0
            interrupts_per_sec = 0.0
            softirqs_per_sec = 0.0
            curr_stats = psutil.cpu_stats()
            now = time.time()
            if self._prev_stats is not None and (now - self._prev_time) > 0:
                elapsed = now - self._prev_time
                ctx_switches_per_sec = (curr_stats.ctx_switches - self._prev_stats.ctx_switches) / elapsed
                interrupts_per_sec = (curr_stats.interrupts - self._prev_stats.interrupts) / elapsed
                try:
                    si_prev = int(getattr(self._prev_stats, "soft_interrupts", 0))
                    si_curr = int(getattr(curr_stats, "soft_interrupts", 0))
                    softirqs_per_sec = (si_curr - si_prev) / elapsed
                except (TypeError, ValueError):
                    softirqs_per_sec = 0.0
            self._prev_stats = curr_stats
            self._prev_time = now

            metrics = {
                "percent_total": percent_total,
                "percent_per_core": percent_per_core,
                "count_logical": count_logical,
                "count_physical": count_physical,
                "load_avg_1m": load_1,
                "load_avg_5m": load_5,
                "load_avg_15m": load_15,
                "load_avg_normalized_1m": load_1 / count_logical,
                "load_avg_normalized_5m": load_5 / count_logical,
                "load_avg_normalized_15m": load_15 / count_logical,
                "times_user": getattr(cpu_times, "user", 0.0),
                "times_system": getattr(cpu_times, "system", 0.0),
                "times_idle": getattr(cpu_times, "idle", 0.0),
                "times_iowait": getattr(cpu_times, "iowait", 0.0),
                "times_steal": getattr(cpu_times, "steal", 0.0),
                "times_softirq": getattr(cpu_times, "softirq", 0.0),
                "times_irq": getattr(cpu_times, "irq", 0.0),
                "times_nice": getattr(cpu_times, "nice", 0.0),
                "times_guest": getattr(cpu_times, "guest", 0.0),
                "freq_current_mhz": freq.current if freq else 0.0,
                "freq_max_mhz": freq.max if freq else 0.0,
                "ctx_switches_per_sec": max(0.0, ctx_switches_per_sec),
                "interrupts_per_sec": max(0.0, interrupts_per_sec),
                "softirqs_per_sec": max(0.0, softirqs_per_sec),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("cpu collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
