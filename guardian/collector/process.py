from __future__ import annotations

import time
from typing import List

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_PROC_ATTRS = ["pid", "name", "cmdline", "cpu_percent", "memory_percent",
               "memory_info", "status", "num_threads", "num_fds", "username",
               "create_time", "ppid"]


def _proc_dict(p: psutil.Process) -> dict:  # type: ignore[type-arg]
    try:
        info = p.as_dict(attrs=_PROC_ATTRS)
        cmdline = " ".join(info.get("cmdline") or [])[:150]
        mem_info = info.get("memory_info")
        try:
            num_fds = p.num_fds()
        except (psutil.AccessDenied, AttributeError):
            num_fds = -1
        return {
            "pid": info.get("pid", 0),
            "name": info.get("name", ""),
            "cmdline": cmdline,
            "cpu_percent": info.get("cpu_percent") or 0.0,
            "memory_percent": info.get("memory_percent") or 0.0,
            "memory_rss_bytes": mem_info.rss if mem_info else 0,
            "memory_vms_bytes": mem_info.vms if mem_info else 0,
            "status": info.get("status", ""),
            "num_threads": info.get("num_threads") or 0,
            "num_fds": num_fds,
            "username": info.get("username") or "",
            "create_time": info.get("create_time") or 0.0,
            "ppid": info.get("ppid") or 0,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {}


class ProcessCollector(BaseCollector):
    name = "process"

    def __init__(self, top_n: int = 10) -> None:
        self.top_n = top_n

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            all_procs = []
            status_counts = {"running": 0, "sleeping": 0, "disk_sleep": 0, "stopped": 0, "zombie": 0}
            zombies = []
            disk_sleep_procs = []

            for p in psutil.process_iter(["status", "pid", "name", "ppid", "username", "cmdline"]):
                try:
                    info = p.as_dict(attrs=["status", "pid", "name", "ppid", "username", "cmdline"])
                    status = info.get("status", "")
                    if status == psutil.STATUS_RUNNING:
                        status_counts["running"] += 1
                    elif status in (psutil.STATUS_SLEEPING, psutil.STATUS_IDLE):
                        status_counts["sleeping"] += 1
                    elif status == psutil.STATUS_DISK_SLEEP:
                        status_counts["disk_sleep"] += 1
                        cmdline = " ".join(info.get("cmdline") or [])[:150]
                        disk_sleep_procs.append({
                            "pid": info.get("pid", 0),
                            "name": info.get("name", ""),
                            "cmdline": cmdline,
                        })
                    elif status == psutil.STATUS_STOPPED:
                        status_counts["stopped"] += 1
                    elif status == psutil.STATUS_ZOMBIE:
                        status_counts["zombie"] += 1
                        zombies.append({
                            "pid": info.get("pid", 0),
                            "name": info.get("name", ""),
                            "ppid": info.get("ppid", 0),
                            "username": info.get("username", ""),
                        })
                    d = _proc_dict(p)
                    if d:
                        all_procs.append(d)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            top_cpu = sorted(all_procs, key=lambda x: x.get("cpu_percent", 0), reverse=True)[: self.top_n]
            top_mem = sorted(all_procs, key=lambda x: x.get("memory_rss_bytes", 0), reverse=True)[: self.top_n]

            metrics = {
                "total_count": len(all_procs),
                "running": status_counts["running"],
                "sleeping": status_counts["sleeping"],
                "disk_sleep": status_counts["disk_sleep"],
                "stopped": status_counts["stopped"],
                "zombie": status_counts["zombie"],
                "top_cpu": top_cpu,
                "top_memory": top_mem,
                "zombies": zombies,
                "disk_sleep_procs": disk_sleep_procs,
                "top_cpu_total_percent": sum(p.get("cpu_percent", 0) for p in top_cpu),
                "top_mem_total_rss_bytes": sum(p.get("memory_rss_bytes", 0) for p in top_mem),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("process collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
