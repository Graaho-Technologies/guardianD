from __future__ import annotations

import socket
import subprocess
import time
from typing import List

import psutil
import requests

from ..config.schema import AppHealthCheck
from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)


def _check_port(target: str, timeout: int) -> dict:  # type: ignore[type-arg]
    host, port_str = target.rsplit(":", 1)
    port = int(port_str)
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = (time.time() - start) * 1000
            return {"healthy": True, "latency_ms": latency, "status_code": 0, "error": ""}
    except Exception as exc:
        return {"healthy": False, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": str(exc)}


def _check_http(target: str, timeout: int, expected_status: int) -> dict:  # type: ignore[type-arg]
    start = time.time()
    try:
        resp = requests.get(target, timeout=timeout)
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


def _check_process(target: str) -> dict:  # type: ignore[type-arg]
    found = any(p.name() == target for p in psutil.process_iter(["name"]))
    return {"healthy": found, "latency_ms": 0.0, "status_code": 0, "error": "" if found else f"process '{target}' not found"}


def _check_systemd(target: str, timeout: int) -> dict:  # type: ignore[type-arg]
    start = time.time()
    try:
        result = subprocess.run(
            ["systemctl", "is-active", target],
            capture_output=True, text=True, timeout=timeout,
        )
        active = result.stdout.strip() == "active"
        return {
            "healthy": active,
            "latency_ms": (time.time() - start) * 1000,
            "status_code": 0,
            "error": "" if active else f"unit '{target}' is {result.stdout.strip()}",
        }
    except Exception as exc:
        return {"healthy": False, "latency_ms": (time.time() - start) * 1000, "status_code": 0, "error": str(exc)}


class AppHealthCollector(BaseCollector):
    name = "app_health"

    def __init__(self, checks: List[AppHealthCheck]) -> None:
        self.checks = checks

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            results = []
            for chk in self.checks:
                try:
                    if chk.type == "port":
                        r = _check_port(chk.target, chk.timeout_seconds)
                    elif chk.type == "http":
                        r = _check_http(chk.target, chk.timeout_seconds, chk.expected_status_code)
                    elif chk.type == "process":
                        r = _check_process(chk.target)
                    elif chk.type == "systemd_service":
                        r = _check_systemd(chk.target, chk.timeout_seconds)
                    else:
                        r = {"healthy": False, "latency_ms": 0.0, "status_code": 0, "error": f"unknown type: {chk.type}"}
                    r.update({"name": chk.name, "type": chk.type, "target": chk.target, "last_checked": time.time()})
                    results.append(r)
                except Exception as exc:
                    results.append({
                        "name": chk.name, "type": chk.type, "target": chk.target,
                        "healthy": False, "latency_ms": 0.0, "status_code": 0,
                        "error": str(exc), "last_checked": time.time(),
                    })

            healthy_count = sum(1 for r in results if r.get("healthy"))
            metrics = {
                "checks": results,
                "healthy_count": healthy_count,
                "unhealthy_count": len(results) - healthy_count,
                "total_count": len(results),
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("app_health collect error: %s", exc)
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics={}, status="error", error=str(exc))
