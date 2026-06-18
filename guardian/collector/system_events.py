from __future__ import annotations

import re
import subprocess
import time
from typing import Dict, List, Optional, Set, Tuple

import psutil

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_OOM_RE = re.compile(r"Kill(?:ed)? process (\d+) \(([^)]+)\).*?(\d+) kB", re.DOTALL)
_LEVEL_MAP = {
    "0": "emerg", "1": "alert", "2": "crit",
    "3": "err", "4": "warn", "5": "notice",
    "6": "info", "7": "debug",
}
# Levels we retain from dmesg (mirrors the old --level=warn,err,crit,alert,emerg).
_ALERT_LEVELS = {"warn", "err", "crit", "alert", "emerg"}


def _run(cmd: List[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except Exception as exc:
        _log.debug("subprocess %s failed: %s", cmd[0], exc)
        return ""


def _parse_psi(resource: str) -> Dict[str, Dict[str, float]]:
    """Parse /proc/pressure/{resource}. Returns {} on FileNotFoundError."""
    result: Dict[str, Dict[str, float]] = {}
    try:
        with open(f"/proc/pressure/{resource}", "r") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                kind = parts[0]  # "some" or "full"
                vals: Dict[str, float] = {}
                for kv in parts[1:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        try:
                            vals[k] = float(v)
                        except ValueError:
                            pass
                result[kind] = vals
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return result


def _build_psi_section(resource: str, has_full: bool) -> Dict:
    data = _parse_psi(resource)
    some = data.get("some", {})
    full = data.get("full", {})
    section: Dict = {
        "some_avg10": some.get("avg10", -1.0),
        "some_avg60": some.get("avg60", -1.0),
        "some_avg300": some.get("avg300", -1.0),
        "some_total_us": int(some.get("total", -1)),
    }
    if has_full:
        section.update({
            "full_avg10": full.get("avg10", -1.0),
            "full_avg60": full.get("avg60", -1.0),
            "full_avg300": full.get("avg300", -1.0),
            "full_total_us": int(full.get("total", -1)),
        })
    return section


class SystemEventsCollector(BaseCollector):
    name = "system_events"

    def __init__(self) -> None:
        self._seen_dmesg: Set[Tuple] = set()
        self._seen_ooms: Set[Tuple[float, int]] = set()
        self._first_collection: bool = True
        self._psi_unavailable_logged: bool = False

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            # Use --raw so each line keeps its real syslog priority prefix
            # (<PRI>[boot_seconds] message). --time-format=iso strips the <N>
            # prefix, which made every line default to "warn" and left the
            # "Kernel Critical Event" alert permanently dead (FIX-10). We parse
            # the level ourselves and keep only warn-and-above lines.
            boot_time = psutil.boot_time()
            dmesg_out = _run(["dmesg", "--raw", "--nopager"], timeout=5)
            all_dmesg: List[Dict] = []
            oom_kills_new: List[Dict] = []
            new_oom_count = 0

            for line in dmesg_out.splitlines():
                try:
                    message = line
                    # <PRI> prefix: syslog level = PRI & 7 (facility in high bits).
                    level: Optional[str] = None
                    pri_match = re.match(r"<(\d+)>", message)
                    if pri_match:
                        pri = int(pri_match.group(1))
                        level = _LEVEL_MAP.get(str(pri & 0x07))
                        message = message[pri_match.end():]

                    # Keep only warn-and-above (matches the old --level filter).
                    if level not in _ALERT_LEVELS:
                        continue

                    # Boot-relative timestamp: [   123.456789]
                    msg_ts = 0.0
                    bracket_match = re.match(r"\s*\[\s*([0-9.]+)\]\s*(.*)", message)
                    if bracket_match:
                        try:
                            msg_ts = boot_time + float(bracket_match.group(1))
                        except ValueError:
                            msg_ts = 0.0
                        message = bracket_match.group(2)
                    message = message.strip()

                    entry = {"timestamp": msg_ts, "level": level, "message": message[:300]}
                    all_dmesg.append(entry)

                    oom_match = _OOM_RE.search(line)
                    if oom_match:
                        pid = int(oom_match.group(1))
                        proc_name = oom_match.group(2)
                        pages_freed = int(oom_match.group(3))
                        key = (msg_ts, pid)
                        if key not in self._seen_ooms:
                            self._seen_ooms.add(key)
                            if not self._first_collection:
                                new_oom_count += 1
                                oom_kills_new.append({
                                    "timestamp": msg_ts,
                                    "process_name": proc_name,
                                    "pid": pid,
                                    "pages_freed": pages_freed,
                                    "message": message[:300],
                                })
                except Exception:
                    continue

            # Bound OOM seen set
            if len(self._seen_ooms) > 1000:
                self._seen_ooms = set(list(self._seen_ooms)[-500:])

            # dmesg dedup — seed on first collection so old messages don't re-alert
            dmesg_errors_new: List[Dict] = []
            dmesg_error_count_new = 0
            for entry in all_dmesg:
                key = (entry["timestamp"], entry["message"][:50])
                if key not in self._seen_dmesg:
                    self._seen_dmesg.add(key)
                    if not self._first_collection:
                        dmesg_errors_new.append(entry)
                        dmesg_error_count_new += 1

            if len(self._seen_dmesg) > 5000:
                # Keep only the most recent half to bound memory
                items = list(self._seen_dmesg)
                self._seen_dmesg = set(items[len(items) // 2:])

            if self._first_collection:
                self._first_collection = False

            # Systemd failed units
            failed_units: List[Dict] = []
            systemctl_out = _run(
                ["systemctl", "--failed", "--no-legend", "--plain"], timeout=5
            )
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

            # PSI
            psi_available = False
            psi: Dict = {}
            try:
                cpu_raw = _parse_psi("cpu")
                if cpu_raw:
                    psi_available = True
                    psi = {
                        "cpu": _build_psi_section("cpu", has_full=False),
                        "memory": _build_psi_section("memory", has_full=True),
                        "io": _build_psi_section("io", has_full=True),
                    }
                else:
                    if not self._psi_unavailable_logged:
                        _log.info("PSI not available. Kernel 4.20+ required.")
                        self._psi_unavailable_logged = True
                    psi = _empty_psi()
            except Exception:
                psi = _empty_psi()

            kernel_version = _run(["uname", "-r"]).strip()
            uptime_seconds = time.time() - boot_time

            metrics = {
                "dmesg_errors_new": dmesg_errors_new,
                "dmesg_error_count_new": dmesg_error_count_new,
                "oom_kills_new": oom_kills_new,
                "oom_kill_count_new": new_oom_count,
                "failed_systemd_units": failed_units,
                "failed_unit_count": len(failed_units),
                "psi_available": psi_available,
                "psi": psi,
                "kernel_version": kernel_version,
                "uptime_seconds": uptime_seconds,
                "boot_time": boot_time,
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("system_events collect error: %s", exc)
            return MetricSnapshot(
                collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc)
            )


def _empty_psi() -> Dict:
    _neg = {"some_avg10": -1.0, "some_avg60": -1.0, "some_avg300": -1.0, "some_total_us": -1}
    _neg_full = {**_neg, "full_avg10": -1.0, "full_avg60": -1.0, "full_avg300": -1.0, "full_total_us": -1}
    return {"cpu": dict(_neg), "memory": dict(_neg_full), "io": dict(_neg_full)}
