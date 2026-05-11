from __future__ import annotations

import re
import subprocess
import time
from typing import Dict, List, Set, Tuple

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_OOM_RE = re.compile(r"Out of memory: Kill process (\d+) \(([^)]+)\)")
_DMESG_LEVEL_RE = re.compile(r"<(\d+)>")
_LEVEL_MAP = {"0": "emerg", "1": "alert", "2": "crit", "3": "err", "4": "warn", "5": "notice"}


def _run(cmd: List[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except Exception as exc:
        _log.debug("subprocess %s failed: %s", cmd[0], exc)
        return ""


class SystemEventsCollector(BaseCollector):
    name = "system_events"

    def __init__(self) -> None:
        self._seen_ooms: Set[Tuple[float, int]] = set()
        self._prev_oom_count: int = 0

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            dmesg_out = _run(["dmesg", "--level=err,warn,crit,alert,emerg", "--time-format=iso", "-T"])
            dmesg_errors: List[Dict] = []
            oom_kills: List[Dict] = []

            for line in dmesg_out.splitlines()[-200:]:
                try:
                    # parse ISO timestamp from dmesg -T --time-format=iso
                    # format: [2024-01-15T14:32:10,123456+00:00] message
                    msg_ts = 0.0
                    message = line
                    bracket_match = re.match(r"\[([^\]]+)\]\s*(.*)", line)
                    if bracket_match:
                        ts_str = bracket_match.group(1)
                        message = bracket_match.group(2)
                        try:
                            from datetime import datetime, timezone
                            msg_ts = datetime.fromisoformat(ts_str.replace(",", ".")).timestamp()
                        except Exception:
                            msg_ts = 0.0

                    level = "err"
                    level_match = _DMESG_LEVEL_RE.match(message)
                    if level_match:
                        level = _LEVEL_MAP.get(level_match.group(1), "err")
                        message = message[level_match.end():].strip()

                    dmesg_errors.append({"timestamp": msg_ts, "level": level, "message": message[:300]})

                    oom_match = _OOM_RE.search(line)
                    if oom_match:
                        pid = int(oom_match.group(1))
                        proc_name = oom_match.group(2)
                        key = (msg_ts, pid)
                        oom_kills.append({"timestamp": msg_ts, "process_name": proc_name, "pid": pid, "message": message[:300]})
                except Exception:
                    continue

            dmesg_errors = dmesg_errors[-50:]

            # OOM kills since last cycle
            new_oom_count = 0
            for oom in oom_kills:
                key = (oom["timestamp"], oom["pid"])
                if key not in self._seen_ooms:
                    new_oom_count += 1
                    self._seen_ooms.add(key)
            # Keep seen set bounded
            if len(self._seen_ooms) > 1000:
                self._seen_ooms = set(list(self._seen_ooms)[-500:])

            # Systemd failed units
            failed_units: List[Dict] = []
            systemctl_out = _run(["systemctl", "--failed", "--no-legend", "--plain"])
            for line in systemctl_out.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    failed_units.append({
                        "unit": parts[0],
                        "load": parts[1],
                        "active": parts[2],
                        "sub": parts[3],
                        "description": parts[4] if len(parts) > 4 else "",
                    })

            # Kernel version
            kernel_version = _run(["uname", "-r"]).strip()
            boot_time = psutil.boot_time()
            uptime_seconds = time.time() - boot_time

            metrics = {
                "dmesg_errors": dmesg_errors,
                "oom_kills": oom_kills[-20:],
                "oom_kill_count_new": new_oom_count,
                "failed_systemd_units": failed_units,
                "kernel_version": kernel_version,
                "uptime_seconds": uptime_seconds,
                "boot_time": boot_time,
                "last_reboot_reason": "",
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("system_events collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
