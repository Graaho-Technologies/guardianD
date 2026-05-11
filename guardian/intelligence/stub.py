from __future__ import annotations

from typing import Dict, List

from ..collector.base import MetricSnapshot


class AnomalyDetector:
    """
    Phase 2: Will implement rolling baseline, z-score detection,
    rate-of-change alerts, and trend forecasting.
    """

    def analyze(self, snapshots: Dict[str, MetricSnapshot]) -> List[dict]:  # type: ignore[type-arg]
        """Returns list of anomaly dicts. Empty list until Phase 2."""
        return []

    def update_baseline(self, snapshot: MetricSnapshot) -> None:
        """Update rolling baseline. No-op until Phase 2."""
        pass
