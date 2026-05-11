from __future__ import annotations

import time

import pytest

from guardian.storage.sqlite_store import SQLiteStore
from guardian.config.schema import StorageConfig
from guardian.alerter.base import AlertSeverity

from .conftest import make_snapshot, make_alert


@pytest.fixture
def store(tmp_path):
    cfg = StorageConfig(
        db_path=str(tmp_path / "test.db"),
        log_dir=str(tmp_path),
        metric_retention_days=7,
    )
    return SQLiteStore(cfg)


def test_insert_and_query_snapshot(store):
    snap = make_snapshot("cpu", {"percent_total": 50.0})
    store.insert_snapshot(snap)

    results = store.query_snapshots("cpu", since=0, until=time.time() + 1)
    assert len(results) == 1
    assert results[0]["collector_name"] == "cpu"


def test_insert_and_query_alert(store):
    alert = make_alert(AlertSeverity.CRITICAL)
    store.insert_alert(alert, sent_to=["slack"])

    results = store.query_alerts(since=0, until=time.time() + 1)
    assert len(results) == 1
    assert results[0]["severity"] == "CRITICAL"
    assert results[0]["sent_to"] == "slack"


def test_latest_snapshot(store):
    snap1 = make_snapshot("cpu", {"percent_total": 30.0})
    snap2 = make_snapshot("cpu", {"percent_total": 70.0})
    store.insert_snapshot(snap1)
    store.insert_snapshot(snap2)

    latest = store.latest_snapshot("cpu")
    assert latest is not None
    import json
    metrics = json.loads(latest["metrics_json"])
    assert metrics["percent_total"] == 70.0


def test_prune_old_data(store):
    old_snap = make_snapshot("cpu", {"percent_total": 10.0})
    old_snap.timestamp = time.time() - 10 * 86400
    store.insert_snapshot(old_snap)

    new_snap = make_snapshot("cpu", {"percent_total": 20.0})
    store.insert_snapshot(new_snap)

    deleted = store.prune_old_data(retention_days=7)
    assert deleted >= 1

    results = store.query_snapshots("cpu", since=0, until=time.time() + 1)
    assert len(results) == 1


def test_query_alerts_by_severity(store):
    alert_warn = make_alert(AlertSeverity.WARN)
    alert_crit = make_alert(AlertSeverity.CRITICAL)
    store.insert_alert(alert_warn, [])
    store.insert_alert(alert_crit, [])

    crit_results = store.query_alerts(0, time.time() + 1, severity="CRITICAL")
    assert all(r["severity"] == "CRITICAL" for r in crit_results)
    assert len(crit_results) == 1
