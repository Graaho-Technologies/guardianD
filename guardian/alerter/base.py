from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple

SEVERITY_ORDER: Dict[str, int] = {"INFO": 0, "WARN": 1, "CRITICAL": 2, "EMERGENCY": 3}


class AlertSeverity(Enum):
    INFO = 0
    WARN = 1
    CRITICAL = 2
    EMERGENCY = 3


def make_fingerprint(category: str, title: str) -> str:
    return hashlib.sha256(f"{category}|{title}".encode()).hexdigest()[:16]


def mask_account_id(account_id: str) -> str:
    """Redact the middle of an AWS account ID as ``starting****ending``.

    Keeps the first and last 4 characters so the account is still recognisable,
    but the full 12-digit ID never appears in any alert, log, or metric label.
    Values too short to redact meaningfully (<= 8 chars) are returned unchanged.
    """
    s = str(account_id or "")
    if len(s) <= 8:
        return s
    return f"{s[:4]}****{s[-4:]}"


def resolve_account(config: Any, snapshots: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve (aws_account_id, aws_account_name) for an alert.

    Account ID: explicit config value wins; otherwise fall back to the value the
    EC2 collector pulled from the IMDS instance-identity document. The ID is
    returned **redacted** (``mask_account_id``) so the full value is never
    surfaced. Account name is not exposed by IMDS, so it comes only from config.
    """
    account_id = getattr(config, "aws_account_id", "") or ""
    if not account_id and snapshots:
        ec2 = snapshots.get("ec2")
        if ec2 and getattr(ec2, "metrics", None) and ec2.metrics.get("aws_account_id"):
            account_id = str(ec2.metrics["aws_account_id"])
    account_name = getattr(config, "aws_account_name", "") or ""
    return mask_account_id(account_id), account_name


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
    aws_account_id: str = ""
    aws_account_name: str = ""
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
