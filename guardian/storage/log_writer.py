from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

from ..alerter.base import Alert
from ..collector.base import MetricSnapshot
from ..config.schema import StorageConfig
from ..utils.logger import get_logger

_log = get_logger(__name__)
_VERSION = "0.1.0"


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime


class LogWriter:
    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        os.makedirs(config.log_dir, exist_ok=True)
        max_bytes = config.log_rotation_mb * 1024 * 1024

        self._human_logger = logging.getLogger("guardian.human")
        if not self._human_logger.handlers:
            h = logging.handlers.RotatingFileHandler(
                os.path.join(config.log_dir, "guardian.log"),
                maxBytes=max_bytes, backupCount=10,
            )
            h.setFormatter(_UTCFormatter(
                "%(asctime)s.%(msecs)03d UTC %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            self._human_logger.addHandler(h)
            self._human_logger.setLevel(logging.DEBUG)
            self._human_logger.propagate = False

        self._json_logger = logging.getLogger("guardian.jsonl")
        if not self._json_logger.handlers:
            jh = logging.handlers.RotatingFileHandler(
                os.path.join(config.log_dir, "guardian.jsonl"),
                maxBytes=max_bytes, backupCount=10,
            )
            jh.setFormatter(logging.Formatter("%(message)s"))
            self._json_logger.addHandler(jh)
            self._json_logger.setLevel(logging.DEBUG)
            self._json_logger.propagate = False

    def log_alert(self, alert: Alert) -> None:
        sev = alert.severity.name
        metric_summary = " ".join(f"{k}={v}" for k, v in list(alert.metrics.items())[:5])
        self._human_logger.info(
            "[%s] [%s] %s | %s", sev, alert.category, alert.title, metric_summary
        )
        ts_iso = datetime.fromtimestamp(alert.timestamp, tz=timezone.utc).isoformat()
        record = {
            "ts": alert.timestamp,
            "ts_iso": ts_iso,
            "severity": sev,
            "category": alert.category,
            "title": alert.title,
            "message": alert.message,
            "metrics": alert.metrics,
            "instance_id": alert.instance_id,
            "instance_name": alert.instance_name,
            "environment": alert.environment,
            "aws_account_id": alert.aws_account_id,
            "aws_account_name": alert.aws_account_name,
            "fingerprint": alert.fingerprint,
            "is_recovery": alert.is_recovery,
            "version": _VERSION,
        }
        self._json_logger.info(json.dumps(record))

    def log_snapshot(self, snapshot: MetricSnapshot, level: str = "DEBUG") -> None:
        record = {
            "ts": snapshot.timestamp,
            "collector": snapshot.collector_name,
            "status": snapshot.status,
            "metrics": snapshot.metrics,
        }
        if level.upper() == "DEBUG":
            self._json_logger.debug(json.dumps(record))
        else:
            self._json_logger.info(json.dumps(record))

    def log_event(self, level: str, message: str, **kwargs: object) -> None:
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        full_msg = f"[{level}] {message}" + (f" {extra}" if extra else "")
        log_fn = {
            "DEBUG": self._human_logger.debug,
            "INFO": self._human_logger.info,
            "WARNING": self._human_logger.warning,
            "WARN": self._human_logger.warning,
            "ERROR": self._human_logger.error,
            "CRITICAL": self._human_logger.critical,
        }.get(level.upper(), self._human_logger.info)
        log_fn(full_msg)

    def log_intelligence(self, result: Dict[str, Any]) -> None:
        record = {
            "ts": time.time(),
            "type": "intelligence",
            **result,
        }
        self._json_logger.info(json.dumps(record, default=str))
