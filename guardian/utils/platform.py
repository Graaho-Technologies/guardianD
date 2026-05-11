from __future__ import annotations

import platform
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import psutil


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_ec2(timeout: float = 2.0) -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "10"},
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def get_hostname() -> str:
    return socket.gethostname()


def get_boot_time() -> float:
    return psutil.boot_time()


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.2f} PB"


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
