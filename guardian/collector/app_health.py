from __future__ import annotations

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import psutil
import requests

from ..config.schema import AppHealthCheck
from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)


def _check_port(target: str, timeout: int) -> Dict:  # type: ignore[type-arg]
    host, port_str = target.rsplit(":", 1)
    port = int(port_str)
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"healthy": True, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": ""}
    except Exception as exc:
        return {"healthy": False, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": str(exc)}


def _check_http(target: str, timeout: int, expected_status: int, headers: Dict) -> Dict:  # type: ignore[type-arg]
    start = time.time()
    try:
        resp = requests.get(target, timeout=timeout, headers=headers, verify=True)
        latency = (time.time() - start) * 1000
        healthy = resp.status_code == expected_status
        return {
            "healthy": healthy,
            "latency_ms": latency,
            "status_code": resp.status_code,
            "error": "" if healthy else f"expected {expected_status}, got {resp.status_code}",
        }
    except Exception as exc:
        return {"healthy": False, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": str(exc)}


def _check_process(target: str) -> Dict:  # type: ignore[type-arg]
    found = any(p.name() == target for p in psutil.process_iter(["name"]))
    return {
        "healthy": found, "latency_ms": 0.0, "status_code": 0,
        "error": "" if found else f"process '{target}' not found",
    }


def _check_systemd(target: str, timeout: int) -> Dict:  # type: ignore[type-arg]
    start = time.time()
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", target],
            capture_output=True, text=True, timeout=timeout,
        )
        active = result.returncode == 0
        return {
            "healthy": active,
            "latency_ms": (time.time() - start) * 1000,
            "status_code": 0,
            "error": "" if active else f"unit '{target}' is not active",
        }
    except Exception as exc:
        return {"healthy": False, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": str(exc)}


class AppHealthCollector(BaseCollector):
    name = "app_health"

    def __init__(self, checks: List[AppHealthCheck]) -> None:
        self.checks = checks
        self._last_checked: Dict[str, float] = {}
        self._failure_counts: Dict[str, int] = {}
        self._last_results: Dict[str, Dict] = {}  # type: ignore[type-arg]

    def _run_check(self, chk: AppHealthCheck) -> Dict:  # type: ignore[type-arg]
        if chk.type == "port":
            return _check_port(chk.target, chk.timeout_seconds)
        elif chk.type == "http":
            return _check_http(chk.target, chk.timeout_seconds, chk.expected_status_code, chk.headers)
        elif chk.type == "process":
            return _check_process(chk.target)
        elif chk.type == "systemd_service":
            return _check_systemd(chk.target, chk.timeout_seconds)
        else:
            return {"healthy": False, "latency_ms": 0.0, "status_code": 0, "error": f"unknown type: {chk.type}"}

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            now = time.time()
            results: List[Dict] = []  # type: ignore[type-arg]

            checks_to_run = [
                chk for chk in self.checks
                if (now - self._last_checked.get(chk.name, 0.0)) >= chk.interval_seconds
            ]
            checks_cached = [chk for chk in self.checks if chk not in checks_to_run]

            if checks_to_run:
                with ThreadPoolExecutor(max_workers=len(checks_to_run)) as pool:
                    futures = {pool.submit(self._run_check, chk): chk for chk in checks_to_run}
                    for fut, chk in futures.items():
                        try:
                            r = fut.result(timeout=chk.timeout_seconds + 5)
                        except Exception as exc:
                            r = {
                                "healthy": False, "latency_ms": 0.0,
                                "status_code": 0, "error": str(exc),
                            }
                        r.update({
                            "name": chk.name, "type": chk.type,
                            "target": chk.target, "last_checked": now,
                        })
                        if r["healthy"]:
                            self._failure_counts[chk.name] = 0
                        else:
                            self._failure_counts[chk.name] = (
                                self._failure_counts.get(chk.name, 0) + 1
                            )
                        r["consecutive_failures"] = self._failure_counts.get(chk.name, 0)
                        self._last_checked[chk.name] = now
                        self._last_results[chk.name] = r
                        results.append(r)

            for chk in checks_cached:
                if chk.name in self._last_results:
                    results.append(self._last_results[chk.name])

            healthy_count = sum(1 for r in results if r.get("healthy"))
            metrics = {
                "checks": results,
                "healthy_count": healthy_count,
                "unhealthy_count": len(results) - healthy_count,
                "total_count": len(results),
                "all_healthy": healthy_count == len(results),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("app_health collect error: %s", exc)
            return MetricSnapshot(
                collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc)
            )
