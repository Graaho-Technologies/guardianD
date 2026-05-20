from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from ..config.schema import TelegramConfig
from ..utils.logger import get_logger
from .base import Alert, AlertSeverity, BaseAlerter

_log = get_logger(__name__)

_MDV2_ESCAPE = str.maketrans({
    c: f"\\{c}" for c in r"\_*[]()~`>#+-=|{}.!"
})


def escape_mdv2(text: str) -> str:
    return text.translate(_MDV2_ESCAPE)


_EMOJIS = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARN: "⚠️",
    AlertSeverity.CRITICAL: "🚨",
    AlertSeverity.EMERGENCY: "🆘",
}


class TelegramAlerter(BaseAlerter):
    name = "telegram"

    def __init__(self, config: TelegramConfig) -> None:
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
            emoji = _EMOJIS.get(alert.severity, "🔔")
            ts_human = datetime.fromtimestamp(alert.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            metric_lines = "\n".join(
                f"• `{escape_mdv2(str(k))}`: {escape_mdv2(str(v))}"
                for k, v in list(alert.metrics.items())[:10]
            )

            prefix = ""
            if alert.is_recovery:
                prefix = "✅ *RECOVERED* — "
            elif alert.severity == AlertSeverity.EMERGENCY:
                prefix = "🆘 URGENT: "

            text = (
                f"{prefix}{emoji} *{alert.severity.name}* — {escape_mdv2(alert.title)}\n\n"
                f"{escape_mdv2(alert.message)}\n\n"
                f"🖥 *Instance*: `{escape_mdv2(alert.instance_name)}`\n"
                f"🌍 *Environment*: `{escape_mdv2(alert.environment)}`\n"
                f"🕐 *Time*: {escape_mdv2(ts_human)}\n"
                f"🆔 *ID*: `{escape_mdv2(alert.instance_id)}`\n"
            )
            if metric_lines:
                text += f"\n*Triggering Metrics:*\n{metric_lines}"

            url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
            for attempt in range(3):
                resp = requests.post(
                    url,
                    json={"chat_id": self.config.chat_id, "text": text, "parse_mode": "MarkdownV2"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                    _log.warning("telegram rate-limited, retry after %ds", retry_after)
                    time.sleep(retry_after)
                    continue
                _log.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return False
        except Exception as exc:
            _log.error("telegram send error: %s", exc)
            return False
