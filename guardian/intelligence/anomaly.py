from __future__ import annotations

import socket
import time
import uuid
from typing import Dict, List, Optional

from ..alerter.base import Alert, AlertSeverity, make_fingerprint
from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger
from .baseline import BaselineEngine, STATIC_METRICS

_log = get_logger(__name__)


def _get_nested(d: dict, keys: List[str]) -> Optional[float]:  # type: ignore[type-arg]
    current: object = d
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return float(current)
    return None


def _instance_id(snapshots: Dict[str, MetricSnapshot]) -> str:
    ec2 = snapshots.get("ec2")
    if ec2 and ec2.metrics.get("instance_id"):
        return str(ec2.metrics["instance_id"])
    return socket.gethostname()


class AnomalyDetector:
    """
    Z-score anomaly detection using BaselineEngine stats.
    z = (current - mean) / stddev
    Alert only on positive anomalies for resource metrics.
    """

    MONITORED_METRICS = {
        'cpu.percent_total', 'cpu.times_iowait', 'cpu.times_steal',
        'memory.percent_used', 'memory.swap_sout_per_sec',
        'network.dns_latency_ms',
    }

    def __init__(self, config: GuardianConfig, baseline_engine: BaselineEngine) -> None:
        self.config = config
        self.baseline = baseline_engine

    def analyze(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        if self.baseline.is_warming_up():
            return []

        alerts: List[Alert] = []
        t = self.config.thresholds
        iid = _instance_id(snapshots)

        for metric_path in self.MONITORED_METRICS:
            parts = metric_path.split('.')
            collector = parts[0]
            key_parts = parts[1:]
            metric_key = '.'.join(key_parts)

            full_static_key = metric_path  # already in "collector.metric_key" form
            if full_static_key in STATIC_METRICS:
                continue

            snap = snapshots.get(collector)
            if not snap or snap.status == "error" or not snap.metrics:
                continue

            value = _get_nested(snap.metrics, key_parts)
            if value is None:
                continue

            stats = self.baseline.get_stats(collector, metric_key)
            if not stats:
                continue

            stddev = stats["stddev"]
            if stddev < 0.1:
                continue

            mean = stats["mean"]
            z = (value - mean) / stddev

            if z <= 0:
                continue

            if self._is_above_threshold(collector, metric_key, value):
                continue

            warn_z = t.anomaly_zscore_warn
            crit_z = t.anomaly_zscore_critical

            if z >= crit_z:
                sev = AlertSeverity.CRITICAL
            elif z >= warn_z:
                sev = AlertSeverity.WARN
            else:
                continue

            title = f"Anomaly Detected: {metric_path}"
            a = Alert(
                id=str(uuid.uuid4()),
                severity=sev,
                category="intelligence",
                title=title,
                message=(
                    f"{metric_path} = {value:.2f} (mean={mean:.2f}, "
                    f"stddev={stddev:.2f}, z={z:.2f})"
                ),
                metrics={
                    "metric": metric_path,
                    "value": value,
                    "mean": mean,
                    "stddev": stddev,
                    "z_score": round(z, 3),
                },
                instance_id=iid,
                instance_name=self.config.instance_name or iid,
                environment=self.config.environment,
                timestamp=time.time(),
                fingerprint=make_fingerprint("intelligence", title),
                anomaly_score=round(z, 3),
            )
            alerts.append(a)

        return alerts

    def _is_above_threshold(self, collector: str, metric_key: str, value: float) -> bool:
        t = self.config.thresholds
        threshold_map: Dict[tuple, float] = {
            ('cpu', 'percent_total'): t.cpu_warn,
            ('cpu', 'times_iowait'): t.cpu_iowait_warn,
            ('cpu', 'times_steal'): t.cpu_steal_warn,
            ('memory', 'percent_used'): t.memory_warn,
            ('memory', 'swap_sout_per_sec'): t.swap_sout_warn,
        }
        threshold = threshold_map.get((collector, metric_key))
        if threshold is None:
            return False
        return value >= threshold
