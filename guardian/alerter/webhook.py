from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests

from ..config.schema import WebhookConfig
from ..utils.logger import get_logger
from .base import Alert, BaseAlerter

_log = get_logger(__name__)


class WebhookAlerter(BaseAlerter):
    name = "webhook"

    def __init__(self, config: WebhookConfig) -> None:
        self.config = config
        self.min_severity = config.min_severity

    def is_enabled(self) -> bool:
        return self.config.enabled

    def send(self, alert: Alert) -> bool:
        if not self.config.enabled:
            return False
        if not self.meets_severity_threshold(alert):
            return False
        try:
            ts_iso = datetime.fromtimestamp(alert.timestamp, tz=timezone.utc).isoformat()
            payload = {
                "id": alert.id,
                "severity": alert.severity.name,
                "category": alert.category,
                "title": alert.title,
                "message": alert.message,
                "metrics": alert.metrics,
                "instance_id": alert.instance_id,
                "instance_name": alert.instance_name,
                "environment": alert.environment,
                "timestamp": alert.timestamp,
                "timestamp_iso": ts_iso,
                "is_recovery": alert.is_recovery,
                "fingerprint": alert.fingerprint,
            }
            body = json.dumps(payload)
            headers = {"Content-Type": "application/json"}
            if self.config.secret:
                sig = hmac.new(
                    self.config.secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Guardian-Signature"] = f"sha256={sig}"

            resp = requests.post(self.config.url, data=body, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                return True
            _log.error("webhook send failed: %s %s", resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            _log.error("webhook send error: %s", exc)
            return False
