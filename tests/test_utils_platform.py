from __future__ import annotations

import time

import pytest

from guardian.utils.platform import (
    format_timestamp,
    get_hostname,
    human_bytes,
    human_uptime,
)


def test_human_bytes_bytes():
    assert human_bytes(512) == "512.00 B"


def test_human_bytes_kilobytes():
    assert "KB" in human_bytes(2048)


def test_human_bytes_megabytes():
    result = human_bytes(5 * 1024 * 1024)
    assert "MB" in result


def test_human_bytes_gigabytes():
    result = human_bytes(3 * 1024 ** 3)
    assert "GB" in result


def test_human_uptime_minutes_only():
    result = human_uptime(300)  # 5 minutes
    assert "5m" in result
    assert "h" not in result
    assert "d" not in result


def test_human_uptime_hours_and_minutes():
    result = human_uptime(3900)  # 1h 5m
    assert "1h" in result
    assert "5m" in result


def test_human_uptime_days():
    result = human_uptime(90000)  # 1d 1h 0m
    assert "1d" in result
    assert "1h" in result


def test_human_uptime_zero():
    result = human_uptime(0)
    assert "0m" in result


def test_format_timestamp_returns_iso():
    ts = 1700000000.0
    result = format_timestamp(ts)
    assert "T" in result
    assert result.endswith("+00:00") or result.endswith("Z") or "UTC" in result or "+00" in result


def test_format_timestamp_recent():
    now = time.time()
    result = format_timestamp(now)
    assert "2025" in result or "2026" in result or "2027" in result


def test_get_hostname_returns_string():
    h = get_hostname()
    assert isinstance(h, str)
    assert len(h) > 0
