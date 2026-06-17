from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict

SEVERITY_ORDER: Dict[str, int] = {"INFO": 0, "WARN": 1, "CRITICAL": 2, "EMERGENCY": 3}


class AlertSeverity(Enum):
    INFO = 0
    WARN = 1
    CRITICAL = 2
    EMERGENCY = 3


def make_fingerprint(category: str, title: str) -> str:
    return hashlib.sha256(f"{category}|{title}".encode()).hexdigest()[:16]


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
    is_recovery: bool = False
    anomaly_score: float = 0.0
    forecast_eta_minutes: float = 0.0
    ai_meaning: str = ""     # optional AI-generated, metric-aware "what this means"
    ai_suggestion: str = ""  # optional AI-generated quick-fix steps


class BaseAlerter(ABC):
    name: str = "base"
    min_severity: str = "WARN"

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        ...

    def is_enabled(self) -> bool:
        return True

    def meets_severity_threshold(self, alert: Alert) -> bool:
        return (SEVERITY_ORDER.get(alert.severity.name, 0) >=
                SEVERITY_ORDER.get(self.min_severity, 1))
