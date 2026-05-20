from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Dict, List, Optional

from .alerter.base import BaseAlerter
from .alerter.email_alerter import EmailAlerter
from .alerter.router import AlertRouter
from .alerter.slack import SlackAlerter
from .alerter.telegram import TelegramAlerter
from .alerter.webhook import WebhookAlerter
from .collector.app_health import AppHealthCollector
from .collector.base import BaseCollector, MetricSnapshot
from .collector.cpu import CPUCollector
from .collector.disk import DiskCollector
from .collector.ec2 import EC2Collector
from .collector.memory import MemoryCollector
from .collector.network import NetworkCollector
from .collector.process import ProcessCollector
from .collector.system_events import SystemEventsCollector
from .config.loader import load_config
from .config.schema import GuardianConfig
from .exposition.prometheus import PrometheusExpositionServer
from .exposition.rest_api import RestAPIServer
from .storage.log_writer import LogWriter
from .storage.sqlite_store import SQLiteStore
from .utils.logger import get_logger

_log = get_logger(__name__)

_HEARTBEAT_FILE = "/var/run/guardian/guardian.heartbeat"
_PID_FILE = "/var/run/guardian/guardian.pid"
_MAINTENANCE_INTERVAL = 86400  # 24h


def _load_intelligence(config: GuardianConfig, store: SQLiteStore):  # type: ignore[return]
    """Load intelligence modules. Returns (baseline, anomaly, velocity, forecast, fingerprinter) or all None on failure."""
    if not config.intelligence.enabled:
        return None, None, None, None, None
    try:
        import numpy  # noqa: F401  — validates numpy present before init
        from .intelligence.baseline import BaselineEngine
        from .intelligence.anomaly import AnomalyDetector
        from .intelligence.velocity import VelocityDetector
        from .intelligence.forecast import TrendForecaster
        from .intelligence.fingerprint import BottleneckFingerprinter

        baseline = BaselineEngine(config, store)
        try:
            baseline.load_from_store()
        except Exception as exc:
            _log.warning("Could not load baseline from store: %s", exc)

        anomaly = AnomalyDetector(config, baseline)
        velocity = VelocityDetector(config, baseline)
        forecast = TrendForecaster(config, baseline)
        fingerprinter = BottleneckFingerprinter(config)
        return baseline, anomaly, velocity, forecast, fingerprinter
    except ImportError:
        _log.warning("numpy not installed — intelligence layer disabled. Install with: pip install numpy")
        return None, None, None, None, None
    except Exception as exc:
        _log.error("Intelligence layer init failed: %s", exc)
        return None, None, None, None, None


class GuardianDaemon:
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.config: GuardianConfig = load_config(config_path)
        self._config_lock = threading.Lock()

        self.store = SQLiteStore(self.config.storage)
        self.log_writer = LogWriter(self.config.storage)

        self.collectors: List[BaseCollector] = self._init_collectors()
        self.alerters: List[BaseAlerter] = self._init_alerters()
        self.router = AlertRouter(self.config, self.alerters)

        (self.baseline_engine,
         self.anomaly_detector,
         self.velocity_detector,
         self.forecaster,
         self.fingerprinter) = _load_intelligence(self.config, self.store)

        self.api_server = RestAPIServer(self.config.api, self)
        self.prom_server = PrometheusExpositionServer(self.config.prometheus)

        self._running = False
        self._last_snapshots: Dict[str, MetricSnapshot] = {}
        self._last_collection_ts: float = 0.0
        self._start_time: float = time.time()
        self._last_maintenance: float = 0.0
        self._warmup_logged = False

    def _init_collectors(self) -> List[BaseCollector]:
        cfg = self.config.collector
        collectors: List[BaseCollector] = [
            CPUCollector(),
            MemoryCollector(),
            DiskCollector(),
            NetworkCollector(),
            ProcessCollector(top_n=cfg.process_top_n),
            EC2Collector(timeout=cfg.ec2_imds_timeout, spot_check=cfg.spot_interruption_check),
            SystemEventsCollector(),
        ]
        if self.config.app_health_checks:
            collectors.append(AppHealthCollector(self.config.app_health_checks))
        return [c for c in collectors if c.is_available()]

    def _init_alerters(self) -> List[BaseAlerter]:
        alerters: List[BaseAlerter] = []
        alerts = self.config.alerts
        if alerts.slack.enabled:
            alerters.append(SlackAlerter(alerts.slack))
        if alerts.telegram.enabled:
            alerters.append(TelegramAlerter(alerts.telegram))
        if alerts.email.enabled:
            alerters.append(EmailAlerter(alerts.email))
        if alerts.webhook.enabled:
            alerters.append(WebhookAlerter(alerts.webhook))
        return alerters

    def start(self) -> None:
        self._setup_signal_handlers()
        self._write_pid_file()
        atexit.register(self._remove_pid_file)

        self.api_server.start_background()
        self.prom_server.start_background()

        self._running = True
        self._start_time = time.time()
        self.log_writer.log_event("INFO", "GuardianD started", version="0.1.0", config=self.config_path)

        while self._running:
            cycle_start = time.time()
            try:
                self._run_cycle()
                self._run_maintenance_if_due()
            except Exception as exc:
                _log.error("Collection cycle error: %s", exc, exc_info=True)
                self.log_writer.log_event("ERROR", f"Collection cycle error: {exc}")

            elapsed = time.time() - cycle_start
            with self._config_lock:
                interval = self.config.collector.interval_seconds
            sleep_time = max(0, interval - elapsed)
            time.sleep(sleep_time)

    def _run_cycle(self) -> None:
        snapshots = self._collect_all()
        self._last_snapshots = snapshots
        self._last_collection_ts = time.time()
        self._store_snapshots(snapshots)

        with self._config_lock:
            cfg = self.config

        if cfg.intelligence.enabled and self.baseline_engine is not None:
            self.baseline_engine.update_all(snapshots)
            findings = self.fingerprinter.analyze(snapshots)  # type: ignore[union-attr]
            threshold_alerts = self.router.evaluate(snapshots)
            self._enrich_alerts(threshold_alerts, findings)
            intel_alerts = []

            if not self.baseline_engine.is_warming_up():
                if not self._warmup_logged:
                    self._warmup_logged = True
                    _log.info("Intelligence warm-up complete. Anomaly/velocity/forecast detectors active.")
                intel_alerts += self.anomaly_detector.analyze(snapshots)  # type: ignore[union-attr]
                if cfg.intelligence.velocity_enabled:
                    intel_alerts += self.velocity_detector.analyze(snapshots)  # type: ignore[union-attr]
                if cfg.intelligence.forecast_enabled:
                    intel_alerts += self.forecaster.analyze(snapshots)  # type: ignore[union-attr]

            all_alerts = threshold_alerts + self.router.evaluate_intelligence(intel_alerts)
        else:
            all_alerts = self.router.evaluate(snapshots)

        recovery = self.router._check_recovery(snapshots)
        all_alerts.extend(recovery)
        self.router.dispatch(all_alerts)

        ec2_snap = snapshots.get("ec2")
        instance_id = ""
        if ec2_snap and ec2_snap.metrics:
            instance_id = ec2_snap.metrics.get("instance_id", "")
        label_values = {
            "instance_id": instance_id,
            "instance_name": cfg.instance_name,
            "environment": cfg.environment,
        }
        self.prom_server.update(snapshots, label_values)
        self._write_heartbeat()

    def _enrich_alerts(self, alerts: list, findings: list) -> None:
        if not findings:
            return
        top = findings[0]
        for alert in alerts:
            alert.message += f"\n\n🔍 Root Cause: {top['diagnosis']}"

    def stop(self) -> None:
        self._running = False
        self.api_server.stop()
        self._remove_pid_file()
        self.log_writer.log_event("INFO", "GuardianD stopped gracefully")

    def _collect_all(self) -> Dict[str, MetricSnapshot]:
        with self._config_lock:
            timeout = max(1, self.config.collector.interval_seconds - 2)
        results: Dict[str, MetricSnapshot] = {}
        def _timed_collect(collector: BaseCollector) -> MetricSnapshot:
            t0 = time.time()
            snap = collector.collect()
            snap.collection_duration_ms = (time.time() - t0) * 1000.0
            return snap

        with ThreadPoolExecutor(max_workers=len(self.collectors)) as pool:
            futures = {pool.submit(_timed_collect, c): c for c in self.collectors}
            for fut, collector in futures.items():
                try:
                    snap = fut.result(timeout=timeout)
                    results[snap.collector_name] = snap
                except FuturesTimeoutError:
                    _log.warning("collector %s timed out", collector.name)
                except Exception as exc:
                    _log.error("collector %s error: %s", collector.name, exc)
        return results

    def _store_snapshots(self, snapshots: Dict[str, MetricSnapshot]) -> None:
        for snap in snapshots.values():
            try:
                self.store.insert_snapshot(snap)
            except Exception as exc:
                _log.error("store snapshot error: %s", exc)

    def _write_heartbeat(self) -> None:
        try:
            os.makedirs(os.path.dirname(_HEARTBEAT_FILE), exist_ok=True)
            with open(_HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception as exc:
            _log.warning("heartbeat write error: %s", exc)

    def _run_maintenance_if_due(self) -> None:
        now = time.time()
        if now - self._last_maintenance < _MAINTENANCE_INTERVAL:
            return
        try:
            with self._config_lock:
                storage = self.config.storage
            deleted = self.store.prune_old_data(
                metric_days=storage.metric_retention_days,
                alert_days=None,
                baseline_days=storage.baseline_retention_days,
            )
            if self.baseline_engine is not None:
                self.baseline_engine.flush_to_store()
            self.log_writer.log_event("INFO", f"Maintenance: pruned {deleted} old rows")
            self._last_maintenance = now
        except Exception as exc:
            _log.error("maintenance error: %s", exc)

    def _setup_signal_handlers(self) -> None:
        def _stop(signum: int, frame: object) -> None:
            _log.info("signal %s received, stopping", signum)
            self.stop()

        def _reload(signum: int, frame: object) -> None:
            _log.info("SIGHUP received, reloading config")
            try:
                new_cfg = load_config(self.config_path)
                new_alerters = self._init_alerters_from(new_cfg)
                with self._config_lock:
                    self.config = new_cfg
                    self.alerters = new_alerters
                    self.router = AlertRouter(self.config, self.alerters)
                self.log_writer.log_event("INFO", "Config reloaded")
            except Exception as exc:
                _log.error("config reload error: %s", exc)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGHUP, _reload)

    def _init_alerters_from(self, cfg: GuardianConfig) -> List[BaseAlerter]:
        alerters: List[BaseAlerter] = []
        alerts = cfg.alerts
        if alerts.slack.enabled:
            alerters.append(SlackAlerter(alerts.slack))
        if alerts.telegram.enabled:
            alerters.append(TelegramAlerter(alerts.telegram))
        if alerts.email.enabled:
            alerters.append(EmailAlerter(alerts.email))
        if alerts.webhook.enabled:
            alerters.append(WebhookAlerter(alerts.webhook))
        return alerters

    def _write_pid_file(self) -> None:
        try:
            os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
            with open(_PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception as exc:
            _log.warning("pid file write error: %s", exc)

    def _remove_pid_file(self) -> None:
        try:
            if os.path.exists(_PID_FILE):
                os.remove(_PID_FILE)
        except Exception:
            pass


def cli_entry() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="GuardianD — EC2 Observability Daemon")
    parser.add_argument("--config", default="/etc/guardian/guardian.yaml", help="Path to config file")
    parser.add_argument("--daemon", action="store_true", help="Fork to background (Linux only)")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"GuardianD {__version__}")
        sys.exit(0)

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            sys.exit(0)

    daemon = GuardianDaemon(args.config)
    daemon.start()


if __name__ == "__main__":
    cli_entry()
