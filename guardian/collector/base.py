from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricSnapshot:
    collector_name: str
    timestamp: float
    metrics: dict  # type: ignore[type-arg]
    status: str = "ok"
    error: str = ""
    collection_duration_ms: float = 0.0


class BaseCollector(ABC):
    name: str = "base"

    @abstractmethod
    def collect(self) -> MetricSnapshot:
        """Collect metrics and return snapshot. Must never raise — catch internally."""
        ...

    def is_available(self) -> bool:
        return True
