from __future__ import annotations

import json
import os
import signal
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from ..alerter.base import Alert, AlertSeverity
from ..config.schema import APIConfig
from ..utils.logger import get_logger

if TYPE_CHECKING:
    pass

_log = get_logger(__name__)

_REDACT = {"webhook_url", "bot_token", "chat_id", "smtp_password", "auth_token", "secret"}


def _redact_config(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: "***REDACTED***" if k in _REDACT else _redact_config(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_config(i) for i in obj]
    return obj


def _dataclass_to_dict(obj: Any) -> Any:
    import dataclasses
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _dataclass_to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_dataclass_to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    server: "RestAPIServer"  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:
        _log.debug("REST %s", fmt % args)

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        token = self.server.api_config.auth_token
        if not token:
            client_ip = self.client_address[0]
            if client_ip not in ("127.0.0.1", "::1"):
                self._json_response({"error": "forbidden"}, 403)
                return False
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header == f"Bearer {token}":
            return True
        self._json_response({"error": "unauthorized"}, 401)
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)
        daemon = self.server.daemon_ref

        # Health/ready endpoints bypass auth
        if path == "/-/healthy":
            self._json_response({"status": "ok"})
            return
        if path == "/-/ready":
            if daemon._last_collection_ts > 0:
                self._json_response({"status": "ready"})
            else:
                self._json_response({"status": "not ready"}, 503)
            return

        if not self._check_auth():
            return

        if path == "/api/v1/status":
            self._handle_status(daemon)
        elif path == "/api/v1/metrics":
            self._handle_metrics(daemon)
        elif path.startswith("/api/v1/metrics/history"):
            self._handle_metrics_history(daemon, qs)
        elif path.startswith("/api/v1/metrics/"):
            cname = path.split("/api/v1/metrics/", 1)[1]
            self._handle_metrics_collector(daemon, cname)
        elif path == "/api/v1/alerts/active":
            self._handle_alerts_active(daemon)
        elif path == "/api/v1/alerts":
            self._handle_alerts(daemon, qs)
        elif path == "/api/v1/health-checks":
            self._handle_health_checks(daemon)
        elif path == "/api/v1/intelligence/baselines":
            self._handle_intelligence_baselines(daemon, qs)
        elif path == "/api/v1/intelligence/anomalies":
            self._handle_intelligence_anomalies(daemon, qs)
        elif path == "/api/v1/config":
            self._handle_config(daemon)
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        path = self.path.rstrip("/")
        daemon = self.server.daemon_ref
        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(body_bytes)
        except Exception:
            body = {}

        if path == "/api/v1/alerts/test":
            self._handle_test_alert(daemon, body)
        elif path == "/api/v1/control/reload":
            self._handle_reload()
        else:
            self._json_response({"error": "not found"}, 404)

    def _handle_status(self, daemon: Any) -> None:
        collectors_info = []
        for snap_name, snap in (daemon._last_snapshots or {}).items():
            collectors_info.append({
                "name": snap_name,
                "last_collected": snap.timestamp,
                "status": snap.status,
                "duration_ms": snap.collection_duration_ms,
            })
        uptime = time.time() - daemon._start_time
        ec2_snap = (daemon._last_snapshots or {}).get("ec2", None)
        instance_id = ec2_snap.metrics.get("instance_id", "") if ec2_snap and ec2_snap.metrics else ""

        active_alerts = list(daemon.router._active.values()) if daemon.router else []

        intel_info: Dict[str, Any] = {}
        baseline = getattr(daemon, "baseline_engine", None)
        if baseline is not None:
            intel_info["enabled"] = True
            intel_info["warming_up"] = baseline.is_warming_up()
        else:
            intel_info["enabled"] = False

        prom_cfg = daemon.config.prometheus
        self._json_response({
            "status": "running",
            "uptime_seconds": uptime,
            "version": "0.1.0",
            "instance_id": instance_id,
            "instance_name": daemon.config.instance_name,
            "environment": daemon.config.environment,
            "collectors": collectors_info,
            "last_collection_ts": daemon._last_collection_ts,
            "active_alert_count": len(active_alerts),
            "intelligence": intel_info,
            "prometheus": {
                "enabled": prom_cfg.enabled,
                "port": prom_cfg.port,
                "host": prom_cfg.host,
            },
        })

    def _handle_metrics(self, daemon: Any) -> None:
        snapshots = {}
        for name, snap in (daemon._last_snapshots or {}).items():
            snapshots[name] = snap.metrics
        self._json_response({"snapshots": snapshots, "timestamp": time.time()})

    def _handle_metrics_collector(self, daemon: Any, cname: str) -> None:
        snap = (daemon._last_snapshots or {}).get(cname)
        if snap is None:
            self._json_response({"error": f"collector '{cname}' not found"}, 404)
            return
        self._json_response({
            "collector_name": snap.collector_name,
            "timestamp": snap.timestamp,
            "status": snap.status,
            "metrics": snap.metrics,
        })

    def _handle_metrics_history(self, daemon: Any, qs: Dict) -> None:
        collector = (qs.get("collector") or [""])[0]
        since = float((qs.get("since") or [str(time.time() - 3600)])[0])
        until = float((qs.get("until") or [str(time.time())])[0])
        limit = int((qs.get("limit") or ["100"])[0])
        rows = daemon.store.query_snapshots(collector, since, until, limit=limit)
        self._json_response({"data": rows})

    def _handle_alerts(self, daemon: Any, qs: Dict) -> None:
        since_str = (qs.get("since") or [None])[0]
        since = float(since_str) if since_str else time.time() - 86400
        until = time.time()
        severity = (qs.get("severity") or [None])[0]
        limit = int((qs.get("limit") or ["50"])[0])
        rows = daemon.store.query_alerts(since, until, severity=severity, limit=limit)
        self._json_response({"alerts": rows, "total": len(rows)})

    def _handle_alerts_active(self, daemon: Any) -> None:
        active = []
        for fp, (alert, first_seen, last_sent) in (daemon.router._active or {}).items():
            active.append({
                "id": alert.id,
                "severity": alert.severity.name,
                "category": alert.category,
                "title": alert.title,
                "message": alert.message,
                "fingerprint": fp,
                "first_seen": first_seen,
                "last_sent": last_sent,
            })
        self._json_response({"alerts": active})

    def _handle_test_alert(self, daemon: Any, body: Dict) -> None:
        sev_name = body.get("severity", "WARN").upper()
        channel = body.get("channel", "all")
        try:
            sev = AlertSeverity[sev_name]
        except KeyError:
            self._json_response({"error": f"unknown severity: {sev_name}"}, 400)
            return

        test_alert = Alert(
            id="test-" + str(time.time()),
            severity=sev,
            category="test",
            title="GuardianD Test Alert",
            message="This is a test alert from guardianctl. If you receive this, alerting is configured correctly.",
            metrics={"test": True},
            instance_id=daemon.config.instance_name,
            instance_name=daemon.config.instance_name,
            environment=daemon.config.environment,
            timestamp=time.time(),
            fingerprint="test-alert",
        )

        sent = False
        for alerter in (daemon.alerters or []):
            if channel not in ("all", alerter.name):
                continue
            try:
                ok = alerter.send(test_alert)
                if ok:
                    sent = True
            except Exception:
                pass
        self._json_response({"sent": sent, "channel": channel})

    def _handle_health_checks(self, daemon: Any) -> None:
        snap = (daemon._last_snapshots or {}).get("app_health")
        if snap:
            self._json_response(snap.metrics)
        else:
            self._json_response({"checks": [], "healthy_count": 0, "unhealthy_count": 0, "total_count": 0})

    def _handle_intelligence_baselines(self, daemon: Any, qs: Dict) -> None:
        baseline = getattr(daemon, "baseline_engine", None)
        if baseline is None:
            self._json_response({"error": "intelligence not enabled"}, 503)
            return
        collector = (qs.get("collector") or [None])[0]
        metric = (qs.get("metric") or [None])[0]
        if collector and metric:
            stats = baseline.get_stats(collector, metric)
            self._json_response({"collector": collector, "metric": metric, "stats": stats})
        elif collector:
            results = {}
            for key, deque_obj in baseline._windows.items():
                c, m = key.split(":", 1)
                if c == collector:
                    stats = baseline.get_stats(c, m)
                    if stats:
                        results[m] = stats
            self._json_response({"collector": collector, "baselines": results})
        else:
            summary = []
            for key in baseline._windows:
                c, m = key.split(":", 1)
                stats = baseline.get_stats(c, m)
                if stats:
                    summary.append({"collector": c, "metric": m, **stats})
            self._json_response({"baselines": summary})

    def _handle_intelligence_anomalies(self, daemon: Any, qs: Dict) -> None:
        since_str = (qs.get("since") or [None])[0]
        since = float(since_str) if since_str else time.time() - 3600
        rows = daemon.store.query_alerts(since, time.time(), limit=200)
        anomalies = [r for r in rows if r.get("category") == "intelligence"]
        self._json_response({"anomalies": anomalies, "total": len(anomalies)})

    def _handle_reload(self) -> None:
        os.kill(os.getpid(), signal.SIGHUP)
        self._json_response({"status": "reloading"})

    def _handle_config(self, daemon: Any) -> None:
        raw = _dataclass_to_dict(daemon.config)
        redacted = _redact_config(raw)
        self._json_response(redacted)


class RestAPIServer:
    def __init__(self, config: APIConfig, daemon_ref: Any) -> None:
        self.api_config = config
        self.daemon_ref = daemon_ref
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start_background(self) -> None:
        if not self.api_config.enabled:
            return
        try:
            self._server = ThreadingHTTPServer((self.api_config.host, self.api_config.port), _Handler)
            self._server.api_config = self.api_config  # type: ignore[attr-defined]
            self._server.daemon_ref = self.daemon_ref  # type: ignore[attr-defined]
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="guardian-api")
            self._thread.start()
            _log.info("REST API listening on %s:%s", self.api_config.host, self.api_config.port)
        except Exception as exc:
            _log.error("REST API failed to start: %s", exc)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
