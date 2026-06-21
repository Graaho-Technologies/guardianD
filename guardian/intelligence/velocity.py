from __future__ import annotations

import socket
import time
import uuid
from typing import Dict, List, Optional

from ..alerter.base import Alert, AlertSeverity, make_fingerprint, resolve_account
from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger
from .baseline import BaselineEngine

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


class VelocityDetector:
    """
    Rate-of-change alerts. Tracks previous value per metric_key.
    pct_change = (current - prev) / prev * 100
    Only alerts on positive velocity (increases).
    Suppressed during warm-up.
    """

    VELOCITY_METRICS: Dict[str, str] = {
        'cpu.percent_total':                    'CPU',
        'memory.percent_used':                  'Memory Usage',
        'memory.swap_sout_per_sec':             'Swap-Out Rate',
        'disk.total_iops':                      'Disk IOPS',
        'network.tcp_connections.established':  'Active TCP Connections',
    }

    def __init__(self, config: GuardianConfig, baseline_engine: BaselineEngine) -> None:
        self.config = config
        self.baseline = baseline_engine
        self._prev_values: Dict[str, float] = {}
        self._collection_count = 0

    def analyze(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        self._collection_count += 1

        if self.baseline.is_warming_up() or self._collection_count < 2:
            self._update_prev(snapshots)
            return []

        alerts: List[Alert] = []
        t = self.config.thresholds
        iid = _instance_id(snapshots)
        acct_id, acct_name = resolve_account(self.config, snapshots)

        for metric_path, label in self.VELOCITY_METRICS.items():
            parts = metric_path.split('.')
            collector = parts[0]
            key_parts = parts[1:]

            snap = snapshots.get(collector)
            if not snap or snap.status == "error" or not snap.metrics:
                continue

            current = _get_nested(snap.metrics, key_parts)
            if current is None:
                self._prev_values.pop(metric_path, None)
                continue

            prev = self._prev_values.get(metric_path)

            if prev is None or prev < 1.0:
                self._prev_values[metric_path] = current
                continue

            # Only alert on CPU velocity if it was below warn threshold before
            if metric_path == 'cpu.percent_total' and prev >= t.cpu_warn:
                self._prev_values[metric_path] = current
                continue

            pct_change = (current - prev) / prev * 100.0

            # Only positive velocity (increases)
            if pct_change <= 0:
                self._prev_values[metric_path] = current
                continue

            # Absolute-magnitude floor: a large % swing on a tiny baseline (e.g.
            # 1.2 -> 16 IOPS = +1283%) is idle-box noise, not an incident. Require
            # the raw delta to clear a per-metric floor before alerting.
            abs_delta = current - prev
            min_abs_delta = t.velocity_min_abs_delta.get(metric_path, 0.0)
            if abs_delta < min_abs_delta:
                self._prev_values[metric_path] = current
                continue

            if pct_change >= t.velocity_spike_critical_pct:
                sev = AlertSeverity.CRITICAL
            elif pct_change >= t.velocity_spike_warn_pct:
                sev = AlertSeverity.WARN
            else:
                self._prev_values[metric_path] = current
                continue

            title = f"Rapid {label} Increase Detected"
            a = Alert(
                id=str(uuid.uuid4()),
                severity=sev,
                category="intelligence",
                title=title,
                message=(
                    f"{label} spiked {pct_change:.1f}% in one interval "
                    f"({prev:.2f} → {current:.2f})"
                ),
                metrics={
                    "metric": metric_path,
                    "previous": round(prev, 3),
                    "current": round(current, 3),
                    "pct_change": round(pct_change, 2),
                    "abs_delta": round(abs_delta, 3),
                    "min_abs_delta": min_abs_delta,
                },
                instance_id=iid,
                instance_name=self.config.instance_name or iid,
                environment=self.config.environment,
                aws_account_id=acct_id,
                aws_account_name=acct_name,
                timestamp=time.time(),
                fingerprint=make_fingerprint("intelligence", title),
            )
            alerts.append(a)
            self._prev_values[metric_path] = current

        return alerts

    def _update_prev(self, snapshots: Dict[str, MetricSnapshot]) -> None:
        for metric_path in self.VELOCITY_METRICS:
            parts = metric_path.split('.')
            collector = parts[0]
            key_parts = parts[1:]
            snap = snapshots.get(collector)
            if not snap or snap.status == "error" or not snap.metrics:
                continue
            value = _get_nested(snap.metrics, key_parts)
            if value is not None:
                self._prev_values[metric_path] = value
