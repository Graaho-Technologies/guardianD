from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config.schema import EmailConfig
from ..utils.logger import get_logger
from .base import Alert, AlertSeverity, BaseAlerter

_log = get_logger(__name__)

_COLORS = {
    AlertSeverity.INFO: "#36a64f",
    AlertSeverity.WARN: "#ffcc00",
    AlertSeverity.CRITICAL: "#e01e5a",
    AlertSeverity.EMERGENCY: "#7c0000",
}

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; margin: 0; padding: 0;">
  <div style="background:{color};padding:16px;color:white;">
    <h2 style="margin:0;">{severity_emoji} {severity}: {title}</h2>
  </div>
  <div style="padding:16px;">
    <table style="border-collapse:collapse;width:100%;">
      <tr><th style="text-align:left;padding:4px 8px;background:#f5f5f5;">Field</th><th style="text-align:left;padding:4px 8px;background:#f5f5f5;">Value</th></tr>
      <tr><td style="padding:4px 8px;">Instance</td><td style="padding:4px 8px;">{instance_name}</td></tr>
      <tr><td style="padding:4px 8px;">Environment</td><td style="padding:4px 8px;">{environment}</td></tr>
      <tr><td style="padding:4px 8px;">Region</td><td style="padding:4px 8px;">{region}</td></tr>
      <tr><td style="padding:4px 8px;">Instance Type</td><td style="padding:4px 8px;">{instance_type}</td></tr>
      <tr><td style="padding:4px 8px;">Time</td><td style="padding:4px 8px;">{timestamp}</td></tr>
    </table>
    <p style="margin-top:16px;">{message}</p>
    <h3>Triggering Metrics</h3>
    <table style="border-collapse:collapse;width:100%;">
      <tr><th style="text-align:left;padding:4px 8px;background:#f5f5f5;">Metric</th><th style="text-align:left;padding:4px 8px;background:#f5f5f5;">Value</th></tr>
      {metric_rows}
    </table>
  </div>
  <div style="padding:8px 16px;background:#f5f5f5;color:#999;font-size:12px;">
    Sent by GuardianD v{version}
  </div>
</body>
</html>
"""

_EMOJIS = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARN: "⚠️",
    AlertSeverity.CRITICAL: "🚨",
    AlertSeverity.EMERGENCY: "🆘",
}


class EmailAlerter(BaseAlerter):
    name = "email"

    def __init__(self, config: EmailConfig, version: str = "0.1.0") -> None:
        self.config = config
        self.version = version

    def is_enabled(self) -> bool:
        return self.config.enabled

    def send(self, alert: Alert) -> bool:
        if not self.config.enabled:
            return False
        if not self.meets_severity_threshold(alert, self.config.min_severity):
            return False
        try:
            color = _COLORS.get(alert.severity, "#cccccc")
            emoji = _EMOJIS.get(alert.severity, "🔔")
            ts_human = datetime.fromtimestamp(alert.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            metric_rows = "".join(
                f"<tr><td style='padding:4px 8px;'>{k}</td><td style='padding:4px 8px;'>{v}</td></tr>"
                for k, v in list(alert.metrics.items())[:20]
            )

            html_body = _HTML_TEMPLATE.format(
                color=color,
                severity_emoji=emoji,
                severity=alert.severity.name,
                title=alert.title,
                instance_name=alert.instance_name,
                environment=alert.environment,
                region=alert.metrics.get("region", ""),
                instance_type=alert.metrics.get("instance_type", ""),
                timestamp=ts_human,
                message=alert.message,
                metric_rows=metric_rows,
                version=self.version,
            )

            prefix = "🆘 URGENT: " if alert.severity == AlertSeverity.EMERGENCY else ""
            subject = f"{prefix}[{alert.severity.name}] {alert.title} — {alert.instance_name} ({alert.environment})"

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.from_addr
            msg["To"] = ", ".join(self.config.to_addrs)
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=15) as smtp:
                if self.config.use_tls:
                    smtp.starttls()
                smtp.login(self.config.smtp_user, self.config.smtp_password)
                smtp.sendmail(self.config.from_addr, self.config.to_addrs, msg.as_string())
            return True
        except smtplib.SMTPException as exc:
            _log.error("email send SMTP error: %s", exc)
            return False
        except Exception as exc:
            _log.error("email send error: %s", exc)
            return False
