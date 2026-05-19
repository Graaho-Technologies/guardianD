from __future__ import annotations

import json
import logging
import os

import pytest

from guardian.alerter.base import AlertSeverity
from guardian.config.schema import StorageConfig
from guardian.storage.log_writer import LogWriter

from .conftest import make_alert, make_snapshot


# ─── Logger isolation ─────────────────────────────────────────────────────────
# LogWriter attaches handlers to the named loggers "guardian.human" and
# "guardian.jsonl". Because Python's logging module keeps these as module-level
# singletons, handlers from one test would persist into the next, pointing at
# the wrong tmp_path.  This autouse fixture strips all handlers before each
# test so every test gets a fresh LogWriter that adds its own handlers.

@pytest.fixture(autouse=True)
def reset_guardian_loggers():
    for name in ("guardian.human", "guardian.jsonl"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            h.flush()
            h.close()
            lg.removeHandler(h)
    yield
    for name in ("guardian.human", "guardian.jsonl"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            h.flush()
            h.close()
            lg.removeHandler(h)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_storage_config(tmp_path) -> StorageConfig:
    cfg = StorageConfig()
    cfg.log_dir = str(tmp_path / "logs")
    cfg.log_rotation_mb = 10
    cfg.log_retention_days = 7
    return cfg


def _log_path(tmp_path) -> str:
    return str(tmp_path / "logs" / "guardian.log")


def _jsonl_path(tmp_path) -> str:
    return str(tmp_path / "logs" / "guardian.jsonl")


def _flush(writer: LogWriter) -> None:
    """Flush all handlers on both internal loggers."""
    for lg_name in ("guardian.human", "guardian.jsonl"):
        lg = logging.getLogger(lg_name)
        for h in lg.handlers:
            h.flush()


# ─── File creation ─────────────────────────────────────────────────────────────

def test_log_writer_creates_log_dir(tmp_path):
    cfg = _make_storage_config(tmp_path)
    LogWriter(cfg)
    assert os.path.isdir(cfg.log_dir)


def test_log_writer_creates_files_on_write(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_event("INFO", "daemon started")
    _flush(writer)
    assert os.path.isfile(_log_path(tmp_path))


# ─── log_alert ─────────────────────────────────────────────────────────────────

def test_log_alert_writes_jsonl(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    alert = make_alert(AlertSeverity.CRITICAL, category="cpu", title="High CPU")
    writer.log_alert(alert)
    _flush(writer)

    jsonl_file = _jsonl_path(tmp_path)
    assert os.path.isfile(jsonl_file)

    with open(jsonl_file) as f:
        lines = [l.strip() for l in f if l.strip()]

    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["severity"] == "CRITICAL"
    assert record["category"] == "cpu"
    assert record["title"] == "High CPU"


def test_log_alert_jsonl_contains_required_fields(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    alert = make_alert(AlertSeverity.WARN, category="memory", title="Low Memory")
    writer.log_alert(alert)
    _flush(writer)

    with open(_jsonl_path(tmp_path)) as f:
        record = json.loads(f.readline())

    for field in ("ts", "ts_iso", "severity", "category", "title", "message",
                  "metrics", "instance_id", "instance_name", "environment",
                  "fingerprint", "is_recovery", "version"):
        assert field in record, f"missing field: {field}"


def test_log_alert_human_log_written(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    alert = make_alert(AlertSeverity.CRITICAL, category="disk", title="Disk Full")
    writer.log_alert(alert)
    _flush(writer)

    with open(_log_path(tmp_path)) as f:
        content = f.read()

    assert "CRITICAL" in content
    assert "Disk Full" in content


def test_log_alert_recovery_flag(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    alert = make_alert(AlertSeverity.INFO, category="cpu", title="CPU Recovered")
    alert.is_recovery = True
    writer.log_alert(alert)
    _flush(writer)

    with open(_jsonl_path(tmp_path)) as f:
        record = json.loads(f.readline())

    assert record["is_recovery"] is True


# ─── log_event ─────────────────────────────────────────────────────────────────

def test_log_event_info_level(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_event("INFO", "daemon started successfully")
    _flush(writer)

    with open(_log_path(tmp_path)) as f:
        content = f.read()

    assert "INFO" in content
    assert "daemon started successfully" in content


def test_log_event_warn_level(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_event("WARN", "config reload failed", reason="parse error")
    _flush(writer)

    with open(_log_path(tmp_path)) as f:
        content = f.read()

    assert "WARN" in content
    assert "config reload failed" in content


def test_log_event_error_level(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_event("ERROR", "collector error", collector="cpu")
    _flush(writer)

    with open(_log_path(tmp_path)) as f:
        content = f.read()

    assert "ERROR" in content


def test_log_event_with_kwargs(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_event("INFO", "metric collected", collector="memory", value=75.0)
    _flush(writer)

    with open(_log_path(tmp_path)) as f:
        content = f.read()

    assert "collector=memory" in content
    assert "value=75.0" in content


# ─── log_snapshot ──────────────────────────────────────────────────────────────

def test_log_snapshot_writes_to_jsonl(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    snap = make_snapshot("cpu", {"percent_total": 55.0})
    writer.log_snapshot(snap, level="INFO")
    _flush(writer)

    with open(_jsonl_path(tmp_path)) as f:
        record = json.loads(f.readline())

    assert record["collector"] == "cpu"
    assert record["status"] == "ok"


# ─── log_intelligence ──────────────────────────────────────────────────────────

def test_log_intelligence_no_exception(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_intelligence({"findings": []})
    writer.log_intelligence({"findings": [{"pattern": "cpu_bound", "confidence": 0.8}]})
    _flush(writer)


def test_log_intelligence_writes_to_jsonl(tmp_path):
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_intelligence({"findings": [{"pattern": "memory_pressure"}]})
    _flush(writer)

    with open(_jsonl_path(tmp_path)) as f:
        record = json.loads(f.readline())

    assert record["type"] == "intelligence"
    assert "findings" in record


def test_log_intelligence_handles_non_serialisable(tmp_path):
    """log_intelligence uses default=str, so non-JSON-native types must not crash."""
    cfg = _make_storage_config(tmp_path)
    writer = LogWriter(cfg)
    writer.log_intelligence({"severity_obj": AlertSeverity.CRITICAL})


# ─── Multiple writers share handlers safely ───────────────────────────────────

def test_two_writers_same_dir_no_duplicate_handlers(tmp_path):
    """
    Creating two LogWriter instances pointing to the same directory must not
    add duplicate handlers (the guard `if not handlers` prevents double-writes).
    """
    cfg = _make_storage_config(tmp_path)
    writer1 = LogWriter(cfg)
    writer2 = LogWriter(cfg)   # guard fires — no new handler added
    alert = make_alert(AlertSeverity.WARN)
    writer1.log_alert(alert)
    writer2.log_alert(alert)
    _flush(writer1)

    with open(_jsonl_path(tmp_path)) as f:
        lines = [l for l in f if l.strip()]

    # Exactly two records (one per log_alert call), not four
    assert len(lines) == 2
