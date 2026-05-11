from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    pass


class AlertSeverity(Enum):
    INFO = 0
    WARN = 1
    CRITICAL = 2
    EMERGENCY = 3


@dataclass
class Alert:
    id: str
    severity: AlertSeverity
    category: str
    title: str
    message: str
    metrics: dict  # type: ignore[type-arg]
    instance_id: str
    instance_name: str
    environment: str
    timestamp: float
    fingerprint: str


class BaseAlerter(ABC):
    name: str = "base"

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        ...

    def is_enabled(self) -> bool:
        return True

    def meets_severity_threshold(self, alert: Alert, min_severity: str) -> bool:
        try:
            min_sev = AlertSeverity[min_severity.upper()]
        except KeyError:
            min_sev = AlertSeverity.WARN
        return alert.severity.value >= min_sev.value
