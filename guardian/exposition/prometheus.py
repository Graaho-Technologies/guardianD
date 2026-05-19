from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

_log = get_logger(__name__)

_PROMETHEUS_AVAILABLE = False
try:
    import prometheus_client
    from prometheus_client import (
        Counter, Gauge, Histogram, Info, REGISTRY,
        GC_COLLECTOR, PLATFORM_COLLECTOR, PROCESS_COLLECTOR,
        start_http_server, generate_latest, CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _log.warning("prometheus-client not installed. Prometheus exposition disabled. Install with: pip install prometheus-client")

_COMMON_LABELS: List[str] = ["instance_id", "instance_name", "environment"]
_metrics: Dict[str, Any] = {}
_metrics_lock = threading.Lock()
_metrics_initialized = False


def _init_metrics(include_process: bool = False) -> bool:
    global _metrics_initialized
    if not _PROMETHEUS_AVAILABLE:
        return False
    with _metrics_lock:
        if _metrics_initialized:
            return True

        if not include_process:
            for col in (GC_COLLECTOR, PLATFORM_COLLECTOR, PROCESS_COLLECTOR):
                try:
                    REGISTRY.unregister(col)
                except Exception:
                    pass

        def _g(name: str, desc: str, extra: Optional[List[str]] = None) -> Any:
            labels = _COMMON_LABELS + (extra or [])
            try:
                return Gauge(name, desc, labels)
            except ValueError:
                return REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]

        def _c(name: str, desc: str, extra: Optional[List[str]] = None) -> Any:
            labels = _COMMON_LABELS + (extra or [])
            try:
                return Counter(name, desc, labels)
            except ValueError:
                return REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]

        def _h(name: str, desc: str, extra: Optional[List[str]] = None,
               buckets: Optional[List[float]] = None) -> Any:
            labels = _COMMON_LABELS + (extra or [])
            kwargs: Dict[str, Any] = {}
            if buckets:
                kwargs["buckets"] = buckets
            try:
                return Histogram(name, desc, labels, **kwargs)
            except ValueError:
                return REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]

        def _i(name: str, desc: str, extra: Optional[List[str]] = None) -> Any:
            labels = _COMMON_LABELS + (extra or [])
            try:
                return Info(name, desc, labels)
            except ValueError:
                return REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]

        def _i_plain(name: str, desc: str) -> Any:
            try:
                return Info(name, desc)
            except ValueError:
                return REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]

        # CPU
        _metrics["g_cpu_usage"] = _g("guardian_cpu_usage_percent", "CPU usage %")
        _metrics["g_cpu_user"] = _g("guardian_cpu_user_percent", "CPU user %")
        _metrics["g_cpu_system"] = _g("guardian_cpu_system_percent", "CPU system %")
        _metrics["g_cpu_iowait"] = _g("guardian_cpu_iowait_percent", "CPU iowait %")
        _metrics["g_cpu_steal"] = _g("guardian_cpu_steal_percent", "CPU steal %")
        _metrics["g_cpu_idle"] = _g("guardian_cpu_idle_percent", "CPU idle %")
        _metrics["g_cpu_load"] = _g("guardian_cpu_load_avg", "Load average", ["period"])
        _metrics["g_cpu_load_norm"] = _g("guardian_cpu_load_normalized", "Normalized load", ["period"])
        _metrics["g_cpu_freq_cur"] = _g("guardian_cpu_freq_current_mhz", "CPU freq MHz")
        _metrics["g_ctx_switches"] = _g("guardian_cpu_ctx_switches_per_second", "Context switches/s")

        # Memory
        _metrics["g_mem_used"] = _g("guardian_memory_used_bytes", "Memory used bytes")
        _metrics["g_mem_available"] = _g("guardian_memory_available_bytes", "Memory available bytes")
        _metrics["g_mem_total"] = _g("guardian_memory_total_bytes", "Memory total bytes")
        _metrics["g_mem_cached"] = _g("guardian_memory_cached_bytes", "Memory cached bytes")
        _metrics["g_mem_ratio"] = _g("guardian_memory_usage_ratio", "Memory usage ratio 0-1")
        _metrics["g_swap_used"] = _g("guardian_swap_used_bytes", "Swap used bytes")
        _metrics["g_swap_ratio"] = _g("guardian_swap_usage_ratio", "Swap usage ratio 0-1")
        _metrics["g_swap_sout"] = _g("guardian_swap_out_pages_per_second", "Swap-out pages/s")
        _metrics["g_dirty"] = _g("guardian_memory_dirty_bytes", "Dirty pages bytes")
        _metrics["g_dirty_ratio"] = _g("guardian_memory_dirty_ratio", "Dirty ratio 0-1")
        _metrics["g_hugepages_used"] = _g("guardian_memory_hugepages_used", "HugePages used")
        _metrics["g_hugepages_total"] = _g("guardian_memory_hugepages_total", "HugePages total")
        _metrics["g_fd_used"] = _g("guardian_fd_used", "FDs allocated")
        _metrics["g_fd_limit"] = _g("guardian_fd_limit", "FD system limit")
        _metrics["g_fd_ratio"] = _g("guardian_fd_usage_ratio", "FD usage ratio 0-1")

        # Disk (per mount)
        _ml = ["mountpoint", "device", "fstype"]
        _metrics["g_disk_used"] = _g("guardian_disk_used_bytes", "Disk used bytes", _ml)
        _metrics["g_disk_free"] = _g("guardian_disk_free_bytes", "Disk free bytes", _ml)
        _metrics["g_disk_total"] = _g("guardian_disk_total_bytes", "Disk total bytes", _ml)
        _metrics["g_disk_ratio"] = _g("guardian_disk_usage_ratio", "Disk usage ratio 0-1", _ml)
        _metrics["g_inode_ratio"] = _g("guardian_disk_inodes_usage_ratio", "Inode usage ratio 0-1", ["mountpoint"])

        # Disk I/O (per disk)
        _dl = ["disk", "disk_type"]
        _metrics["g_disk_read_bps"] = _g("guardian_disk_read_bytes_per_second", "Disk read B/s", _dl)
        _metrics["g_disk_write_bps"] = _g("guardian_disk_write_bytes_per_second", "Disk write B/s", _dl)
        _metrics["g_disk_read_iops"] = _g("guardian_disk_read_iops", "Disk read IOPS", _dl)
        _metrics["g_disk_write_iops"] = _g("guardian_disk_write_iops", "Disk write IOPS", _dl)
        _metrics["g_disk_await"] = _g("guardian_disk_await_milliseconds", "Disk await latency ms", _dl)
        _metrics["g_disk_util"] = _g("guardian_disk_utilization_ratio", "Disk utilization 0-1", _dl)
        _metrics["h_disk_latency"] = _h(
            "guardian_disk_io_latency_milliseconds", "Disk I/O latency ms", _dl,
            buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
        )

        # Network (per interface)
        _metrics["g_net_rx_bps"] = _g("guardian_network_receive_bytes_per_second", "Net RX B/s", ["interface"])
        _metrics["g_net_tx_bps"] = _g("guardian_network_transmit_bytes_per_second", "Net TX B/s", ["interface"])
        _metrics["g_net_err_rate"] = _g("guardian_network_error_rate", "Net error rate", ["interface", "direction"])
        _metrics["g_net_drop_rate"] = _g("guardian_network_drop_rate", "Net drop rate", ["interface", "direction"])
        _metrics["g_tcp_conns"] = _g("guardian_tcp_connections", "TCP connections by state", ["state"])
        _metrics["g_tcp_retrans"] = _g("guardian_tcp_retransmits_per_second", "TCP retransmits/s")
        _metrics["g_dns_latency"] = _g("guardian_dns_latency_milliseconds", "DNS latency ms")
        _metrics["g_dns_healthy"] = _g("guardian_dns_healthy", "DNS healthy 0/1")

        # PSI
        _metrics["g_psi_cpu"] = _g("guardian_psi_cpu_stall_ratio", "PSI CPU stall ratio", ["window", "type"])
        _metrics["g_psi_mem"] = _g("guardian_psi_memory_stall_ratio", "PSI memory stall ratio", ["window", "type"])
        _metrics["g_psi_io"] = _g("guardian_psi_io_stall_ratio", "PSI I/O stall ratio", ["window", "type"])

        # Process
        _metrics["g_proc_total"] = _g("guardian_processes_total", "Total processes")
        _metrics["g_proc_zombie"] = _g("guardian_processes_zombie", "Zombie processes")
        _metrics["g_proc_dsleep"] = _g("guardian_processes_disk_sleep", "D-state processes")

        # EC2
        _metrics["i_ec2"] = _i("guardian_ec2_info", "EC2 instance info")
        _metrics["g_spot_interrupt"] = _g("guardian_ec2_spot_interruption_scheduled", "Spot interruption 0/1")

        # App Health
        _metrics["g_health_status"] = _g(
            "guardian_health_check_status", "Health check 1=healthy 0=unhealthy",
            ["check_name", "check_type"],
        )
        _metrics["g_health_latency"] = _g(
            "guardian_health_check_latency_milliseconds", "Health check latency ms",
            ["check_name"],
        )
        _metrics["h_health_latency"] = _h(
            "guardian_health_check_latency_milliseconds_hist", "Health check latency histogram",
            ["check_name"],
            buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
        )

        # Counters
        _metrics["c_oom_kills"] = _c("guardian_oom_kills_total", "OOM kills total")
        _metrics["c_dmesg_errors"] = _c("guardian_dmesg_errors_total", "dmesg errors total", ["level"])
        _metrics["c_alerts_fired"] = _c("guardian_alerts_fired_total", "Alerts fired total", ["severity", "category"])
        _metrics["c_alerts_recovered"] = _c("guardian_alerts_recovered_total", "Alerts recovered total", ["category"])

        # Collection internals
        _metrics["h_collection_dur"] = _h(
            "guardian_collection_duration_milliseconds", "Collector duration ms",
            ["collector"],
            buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000],
        )
        _metrics["i_build"] = _i_plain("guardian_build_info", "GuardianD build info")

        _metrics_initialized = True
        _log.info("Prometheus metrics registry initialized")
        return True


def update_metrics(snapshots: Dict[str, Any], label_values: Dict[str, str]) -> None:
    """Update all Prometheus gauges from current snapshots. Called every collection cycle."""
    if not _PROMETHEUS_AVAILABLE or not _metrics_initialized:
        return

    lvs = [
        label_values.get("instance_id", ""),
        label_values.get("instance_name", ""),
        label_values.get("environment", ""),
    ]

    try:
        _update_cpu(snapshots.get("cpu"), lvs)
        _update_memory(snapshots.get("memory"), lvs)
        _update_disk(snapshots.get("disk"), lvs)
        _update_network(snapshots.get("network"), lvs)
        _update_psi(snapshots.get("system_events"), lvs)
        _update_process(snapshots.get("process"), lvs)
        _update_ec2(snapshots.get("ec2"), lvs)
        _update_app_health(snapshots.get("app_health"), lvs)
    except Exception as exc:
        _log.error("Prometheus update_metrics error: %s", exc)


def _safe_set(metric_key: str, labels: List[str], value: Any) -> None:
    try:
        if metric_key in _metrics and _metrics[metric_key] is not None:
            _metrics[metric_key].labels(*labels).set(float(value))
    except Exception:
        pass


def _update_cpu(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics
    _safe_set("g_cpu_usage", lvs, m.get("percent_total", 0))
    _safe_set("g_cpu_user", lvs, m.get("times_user", 0))
    _safe_set("g_cpu_system", lvs, m.get("times_system", 0))
    _safe_set("g_cpu_iowait", lvs, m.get("times_iowait", 0))
    _safe_set("g_cpu_steal", lvs, m.get("times_steal", 0))
    _safe_set("g_cpu_idle", lvs, m.get("times_idle", 0))
    _safe_set("g_cpu_freq_cur", lvs, m.get("freq_current_mhz", 0))
    _safe_set("g_ctx_switches", lvs, m.get("ctx_switches_per_sec", 0))

    for period, key in [("1m", "load_avg_1m"), ("5m", "load_avg_5m"), ("15m", "load_avg_15m")]:
        try:
            _metrics["g_cpu_load"].labels(*lvs, period).set(float(m.get(key, 0)))
        except Exception:
            pass

    for period, key in [
        ("1m", "load_avg_normalized_1m"),
        ("5m", "load_avg_normalized_5m"),
        ("15m", "load_avg_normalized_15m"),
    ]:
        try:
            _metrics["g_cpu_load_norm"].labels(*lvs, period).set(float(m.get(key, 0)))
        except Exception:
            pass

    try:
        _metrics["h_collection_dur"].labels(*lvs, "cpu").observe(snap.collection_duration_ms)
    except Exception:
        pass


def _update_memory(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics
    _safe_set("g_mem_used", lvs, m.get("used_bytes", 0))
    _safe_set("g_mem_available", lvs, m.get("available_bytes", 0))
    _safe_set("g_mem_total", lvs, m.get("total_bytes", 0))
    _safe_set("g_mem_cached", lvs, m.get("cached_bytes", 0))
    pct = m.get("percent_used", 0)
    _safe_set("g_mem_ratio", lvs, pct / 100.0 if pct else 0)
    _safe_set("g_swap_used", lvs, m.get("swap_used_bytes", 0))
    swap_pct = m.get("swap_percent", 0)
    _safe_set("g_swap_ratio", lvs, swap_pct / 100.0 if swap_pct else 0)
    _safe_set("g_swap_sout", lvs, m.get("swap_sout_per_sec", 0))
    _safe_set("g_dirty", lvs, m.get("dirty_bytes", 0))
    dirty_ratio = m.get("dirty_ratio_percent", 0)
    _safe_set("g_dirty_ratio", lvs, dirty_ratio / 100.0 if dirty_ratio else 0)
    _safe_set("g_hugepages_used", lvs, m.get("hugepages_total", 0) - m.get("hugepages_free", 0))
    _safe_set("g_hugepages_total", lvs, m.get("hugepages_total", 0))
    _safe_set("g_fd_used", lvs, m.get("fd_allocated", 0))
    _safe_set("g_fd_limit", lvs, m.get("fd_max", 0))
    fd_pct = m.get("fd_percent_used", 0)
    _safe_set("g_fd_ratio", lvs, fd_pct / 100.0 if fd_pct else 0)


def _update_disk(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics

    for mount in m.get("mounts", []):
        mp = mount.get("mountpoint", "")
        dev = mount.get("device", "")
        fst = mount.get("fstype", "")
        lv = lvs + [mp, dev, fst]
        try:
            _metrics["g_disk_used"].labels(*lv).set(float(mount.get("used_bytes", 0)))
            _metrics["g_disk_free"].labels(*lv).set(float(mount.get("free_bytes", 0)))
            _metrics["g_disk_total"].labels(*lv).set(float(mount.get("total_bytes", 0)))
            pct = mount.get("percent_used", 0)
            _metrics["g_disk_ratio"].labels(*lv).set(float(pct) / 100.0)
            ipct = mount.get("inodes_percent", 0)
            _metrics["g_inode_ratio"].labels(*lvs, mp).set(float(ipct) / 100.0)
        except Exception:
            pass

    for disk_name, io in m.get("io", {}).items():
        dtype = io.get("disk_type", "unknown")
        lv = lvs + [disk_name, dtype]
        try:
            _metrics["g_disk_read_bps"].labels(*lv).set(float(io.get("read_bytes_per_sec", 0)))
            _metrics["g_disk_write_bps"].labels(*lv).set(float(io.get("write_bytes_per_sec", 0)))
            _metrics["g_disk_read_iops"].labels(*lv).set(float(io.get("read_ops_per_sec", 0)))
            _metrics["g_disk_write_iops"].labels(*lv).set(float(io.get("write_ops_per_sec", 0)))
            await_ms = float(io.get("await_ms", 0))
            _metrics["g_disk_await"].labels(*lv).set(await_ms)
            util = float(io.get("util_percent", 0))
            _metrics["g_disk_util"].labels(*lv).set(util / 100.0)
            if await_ms > 0:
                _metrics["h_disk_latency"].labels(*lv).observe(await_ms)
        except Exception:
            pass


def _update_network(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics

    for iface, stats in m.get("interfaces", {}).items():
        try:
            _metrics["g_net_rx_bps"].labels(*lvs, iface).set(float(stats.get("bytes_recv_per_sec", 0)))
            _metrics["g_net_tx_bps"].labels(*lvs, iface).set(float(stats.get("bytes_sent_per_sec", 0)))
            _metrics["g_net_err_rate"].labels(*lvs, iface, "in").set(float(stats.get("error_rate_percent", 0)))
            _metrics["g_net_err_rate"].labels(*lvs, iface, "out").set(float(stats.get("errors_out_per_sec", 0)))
            _metrics["g_net_drop_rate"].labels(*lvs, iface, "in").set(float(stats.get("drop_rate_percent", 0)))
            _metrics["g_net_drop_rate"].labels(*lvs, iface, "out").set(float(stats.get("drops_out_per_sec", 0)))
        except Exception:
            pass

    tcp = m.get("tcp_connections", {})
    for state, count in tcp.items():
        try:
            _metrics["g_tcp_conns"].labels(*lvs, state).set(int(count))
        except Exception:
            pass

    tcp_stats = m.get("tcp_stats", {})
    _safe_set("g_tcp_retrans", lvs, tcp_stats.get("retransmits_per_sec", 0))
    _safe_set("g_dns_latency", lvs, m.get("dns_latency_ms", 0))
    _safe_set("g_dns_healthy", lvs, 1 if m.get("dns_healthy", False) else 0)


def _update_psi(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics
    if not m.get("psi_available", False):
        return

    psi = m.get("psi", {})
    _PSI_WINDOWS = [("avg10", "some_avg10"), ("avg60", "some_avg60"), ("avg300", "some_avg300")]
    _PSI_FULL_WINDOWS = [("avg10", "full_avg10"), ("avg60", "full_avg60"), ("avg300", "full_avg300")]

    for resource, gauge_key in [("cpu", "g_psi_cpu"), ("memory", "g_psi_mem"), ("io", "g_psi_io")]:
        psi_r = psi.get(resource, {})
        for window, key in _PSI_WINDOWS:
            val = psi_r.get(key, -1.0)
            if val >= 0:
                try:
                    _metrics[gauge_key].labels(*lvs, window, "some").set(val / 100.0)
                except Exception:
                    pass
        if resource in ("memory", "io"):
            for window, key in _PSI_FULL_WINDOWS:
                val = psi_r.get(key, -1.0)
                if val >= 0:
                    try:
                        _metrics[gauge_key].labels(*lvs, window, "full").set(val / 100.0)
                    except Exception:
                        pass


def _update_process(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics
    _safe_set("g_proc_total", lvs, m.get("total_count", 0))
    _safe_set("g_proc_zombie", lvs, m.get("zombie", 0))
    _safe_set("g_proc_dsleep", lvs, m.get("disk_sleep", 0))


def _update_ec2(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    m = snap.metrics
    if not m.get("is_ec2", False):
        return
    try:
        _metrics["i_ec2"].labels(*lvs).info({
            "instance_id": str(m.get("instance_id", "")),
            "instance_type": str(m.get("instance_type", "")),
            "availability_zone": str(m.get("availability_zone", "")),
            "region": str(m.get("region", "")),
            "lifecycle": str(m.get("instance_lifecycle", "")),
            "ami_id": str(m.get("ami_id", "")),
        })
    except Exception:
        pass
    spot = m.get("spot_interruption", {})
    _safe_set("g_spot_interrupt", lvs, 1 if spot.get("scheduled", False) else 0)


def _update_app_health(snap: Any, lvs: List[str]) -> None:
    if not snap or not snap.metrics:
        return
    for chk in snap.metrics.get("checks", []):
        name = chk.get("name", "")
        ctype = chk.get("type", "")
        healthy = 1 if chk.get("healthy", False) else 0
        latency = float(chk.get("latency_ms", 0))
        try:
            _metrics["g_health_status"].labels(*lvs, name, ctype).set(healthy)
            _metrics["g_health_latency"].labels(*lvs, name).set(latency)
            if latency > 0:
                _metrics["h_health_latency"].labels(*lvs, name).observe(latency)
        except Exception:
            pass


class PrometheusExpositionServer:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._started = False

    def start_background(self) -> None:
        if not self.config.enabled:
            return
        if not _PROMETHEUS_AVAILABLE:
            _log.warning("prometheus-client not installed. Cannot start Prometheus server.")
            return
        _init_metrics(include_process=self.config.include_process_metrics)
        try:
            start_http_server(self.config.port, addr=self.config.host)
            self._started = True
            _log.info("Prometheus metrics listening on %s:%s%s",
                      self.config.host, self.config.port, self.config.path)
        except Exception as exc:
            _log.error("Prometheus server failed to start: %s", exc)

    def update(self, snapshots: Dict[str, Any], label_values: Dict[str, str]) -> None:
        if not self._started or not _PROMETHEUS_AVAILABLE:
            return
        try:
            _metrics["i_build"].info({"version": "0.1.0"})
        except Exception:
            pass
        update_metrics(snapshots, label_values)

    def record_alert(self, severity: str, category: str, is_recovery: bool = False) -> None:
        if not self._started or not _PROMETHEUS_AVAILABLE:
            return
        try:
            if is_recovery:
                _metrics["c_alerts_recovered"].labels("", "", "", category).inc()
            else:
                _metrics["c_alerts_fired"].labels("", "", "", severity, category).inc()
        except Exception:
            pass
