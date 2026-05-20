from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from ..config.schema import SlackConfig
from ..utils.logger import get_logger
from ..utils.retry import retry
from .base import Alert, AlertSeverity, BaseAlerter

_log = get_logger(__name__)

_EMOJIS = {
    AlertSeverity.INFO: ":information_source:",
    AlertSeverity.WARN: ":warning:",
    AlertSeverity.CRITICAL: ":rotating_light:",
    AlertSeverity.EMERGENCY: ":sos:",
}
_COLORS = {
    AlertSeverity.INFO: "#36a64f",
    AlertSeverity.WARN: "#ffcc00",
    AlertSeverity.CRITICAL: "#e01e5a",
    AlertSeverity.EMERGENCY: "#7c0000",
}


class SlackAlerter(BaseAlerter):
    name = "slack"

    def __init__(self, config: SlackConfig) -> None:
        self.config = config
        self.min_severity = config.min_severity

    def is_enabled(self) -> bool:
        return self.config.enabled

    @retry(max_attempts=3, backoff_seconds=2.0, exceptions=(Exception,))
    def _send_with_retry(self, payload: dict) -> bool:
        resp = requests.post(
            self.config.webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            return True
        _log.error("slack send failed: %s %s", resp.status_code, resp.text[:200])
        return False

    def send(self, alert: Alert) -> bool:
        if not self.config.enabled:
            return False
        if not self.meets_severity_threshold(alert):
            return False
        try:
            emoji = _EMOJIS.get(alert.severity, ":bell:")
            color = _COLORS.get(alert.severity, "#cccccc")
            ts_human = datetime.fromtimestamp(alert.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            text = ""
            if alert.severity == AlertSeverity.EMERGENCY:
                text = "<!here> EMERGENCY alert"

            metric_fields = [
                {"title": str(k), "value": str(v), "short": True}
                for k, v in list(alert.metrics.items())[:10]
            ]

            payload = {
                "username": self.config.username,
                "icon_emoji": self.config.icon_emoji,
                "channel": self.config.channel,
                "text": text,
                "attachments": [
                    {
                        "color": color,
                        "blocks": [
                            {
                                "type": "header",
                                "text": {"type": "plain_text", "text": f"{emoji} {alert.severity.name}: {alert.title}"},
                            },
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": alert.message},
                            },
                            {
                                "type": "section",
                                "fields": [
                                    {"type": "mrkdwn", "text": f"*Instance:*\n{alert.instance_name}"},
                                    {"type": "mrkdwn", "text": f"*Environment:*\n{alert.environment}"},
                                    {"type": "mrkdwn", "text": f"*Time:*\n{ts_human}"},
                                    {"type": "mrkdwn", "text": f"*Category:*\n{alert.category}"},
                                ],
                            },
                        ],
                        "fields": metric_fields,
                    }
                ],
            }

            return self._send_with_retry(payload)
        except Exception as exc:
            _log.error("slack send error: %s", exc)
            return False
