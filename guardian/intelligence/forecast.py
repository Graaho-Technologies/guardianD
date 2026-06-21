from __future__ import annotations

import collections
import socket
import time
import uuid
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..alerter.base import Alert, AlertSeverity, make_fingerprint, resolve_account
from ..collector.base import MetricSnapshot
from ..config.schema import GuardianConfig
from ..utils.logger import get_logger
from .baseline import BaselineEngine

_log = get_logger(__name__)

_MAX_HISTORY = 60
_MIN_VALUES = 10


def _instance_id(snapshots: Dict[str, MetricSnapshot]) -> str:
    ec2 = snapshots.get("ec2")
    if ec2 and ec2.metrics.get("instance_id"):
        return str(ec2.metrics["instance_id"])
    return socket.gethostname()


class TrendForecaster:
    """
    Linear regression on recent N data points.
    Projects when metric will breach critical threshold.
    Uses numpy.polyfit(x_minutes, values, 1) → (slope, intercept).
    """

    FORECAST_TARGETS: List[Tuple[str, str, str, str]] = [
        ('disk',   'mounts.*.percent_used',  'disk_critical',   'Disk Space'),
        ('memory', 'percent_used',           'memory_critical', 'Memory Usage'),
        ('memory', 'swap_percent',           'swap_critical',   'Swap Usage'),
    ]

    def __init__(self, config: GuardianConfig, baseline_engine: BaselineEngine) -> None:
        self.config = config
        self.baseline = baseline_engine
        # Per-mountpoint history (disk mounts are lists, not in baseline)
        self._mount_history: Dict[str, collections.deque] = {}

    def analyze(self, snapshots: Dict[str, MetricSnapshot]) -> List[Alert]:
        # Always update mount history
        self._update_mount_history(snapshots)

        if self.baseline.is_warming_up():
            return []

        alerts: List[Alert] = []
        t = self.config.thresholds
        interval = self.config.collector.interval_seconds
        iid = _instance_id(snapshots)
        acct_id, acct_name = resolve_account(self.config, snapshots)
        warn_hours = t.forecast_disk_full_warn_hours
        crit_hours = t.forecast_disk_full_critical_hours

        for collector_name, metric_path, threshold_attr, label in self.FORECAST_TARGETS:
            threshold = float(getattr(t, threshold_attr, 95.0))
            snap = snapshots.get(collector_name)
            if not snap or snap.status == "error" or not snap.metrics:
                continue

            if '*' in metric_path:
                # Disk: iterate per-mountpoint history
                for mountpoint, history in self._mount_history.items():
                    values = list(history)
                    alert = self._project(
                        values, threshold, interval, label,
                        f"{mountpoint} disk", warn_hours, crit_hours,
                        iid, collector_name,
                        f"Disk Space Forecast: {mountpoint}",
                        acct_id, acct_name,
                    )
                    if alert:
                        alerts.append(alert)
            else:
                # Scalar metric via baseline
                values = self.baseline.get_recent_values(
                    collector_name, metric_path, _MAX_HISTORY
                )
                if values is None:
                    continue
                alert = self._project(
                    values, threshold, interval, label,
                    f"{collector_name}.{metric_path}", warn_hours, crit_hours,
                    iid, collector_name,
                    f"{label} Forecast",
                    acct_id, acct_name,
                )
                if alert:
                    alerts.append(alert)

        return alerts

    def _project(
        self,
        values: List[float],
        threshold: float,
        interval_seconds: int,
        label: str,
        metric_desc: str,
        warn_hours: float,
        crit_hours: float,
        instance_id: str,
        collector: str,
        title: str,
        aws_account_id: str = "",
        aws_account_name: str = "",
    ) -> Optional[Alert]:
        n = min(len(values), _MAX_HISTORY)
        values = values[-n:]
        # Require a meaningful history before fitting — a line on ~100s of data
        # projected hours ahead invents trends. Configurable, floored at _MIN_VALUES.
        min_samples = max(_MIN_VALUES, self.config.intelligence.forecast_min_samples)
        if len(values) < min_samples:
            return None

        x = np.arange(len(values))
        x_minutes = x * interval_seconds / 60.0

        try:
            slope, intercept = np.polyfit(x_minutes, values, 1)
        except Exception:
            return None

        if slope <= 0:
            return None

        # Goodness-of-fit gate: polyfit returns a slope even for pure noise, so a
        # slight positive drift on a non-monotonic series (logs rotate, caches
        # grow/shrink) would project a false "disk will fill". Only forecast when
        # the linear fit explains enough variance (R²).
        y = np.asarray(values, dtype=float)
        pred = slope * x_minutes + intercept
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        if ss_tot <= 0.0:
            return None  # perfectly flat — no trend
        r2 = 1.0 - (ss_res / ss_tot)
        if r2 < self.config.intelligence.forecast_min_r2:
            return None

        current = values[-1]
        eta_minutes = (threshold - current) / slope
        if eta_minutes <= 0:
            return None

        eta_hours = eta_minutes / 60.0

        if eta_hours <= crit_hours:
            sev = AlertSeverity.CRITICAL
        elif eta_hours <= warn_hours:
            sev = AlertSeverity.WARN
        else:
            return None

        rate_per_min = slope
        a = Alert(
            id=str(uuid.uuid4()),
            severity=sev,
            category="intelligence",
            title=title,
            message=(
                f"{label} at {current:.1f}%, growing +{rate_per_min:.3f}%/min. "
                f"Will reach {threshold:.0f}% in {eta_hours:.1f}h."
            ),
            metrics={
                "metric": metric_desc,
                "current_value": round(current, 2),
                "threshold": threshold,
                "rate_per_min": round(rate_per_min, 4),
                "eta_minutes": round(eta_minutes, 1),
                "eta_hours": round(eta_hours, 2),
                "r2": round(r2, 3),
            },
            instance_id=instance_id,
            instance_name=self.config.instance_name or instance_id,
            environment=self.config.environment,
            aws_account_id=aws_account_id,
            aws_account_name=aws_account_name,
            timestamp=time.time(),
            fingerprint=make_fingerprint("intelligence", title),
            forecast_eta_minutes=round(eta_minutes, 1),
        )
        return a

    def _update_mount_history(self, snapshots: Dict[str, MetricSnapshot]) -> None:
        disk = snapshots.get("disk")
        if not disk or disk.status == "error" or not disk.metrics:
            return
        for mount in disk.metrics.get("mounts", []):
            mp = mount.get("mountpoint", "")
            pct = mount.get("percent_used")
            if mp and pct is not None:
                if mp not in self._mount_history:
                    self._mount_history[mp] = collections.deque(maxlen=_MAX_HISTORY)
                self._mount_history[mp].append(float(pct))
