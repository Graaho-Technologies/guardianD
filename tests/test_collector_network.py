from __future__ import annotations

from unittest.mock import MagicMock

from guardian.collector.network import NetworkCollector


def _mock_net_counters(**kwargs):
    m = MagicMock()
    m.bytes_sent = kwargs.get("bytes_sent", 0)
    m.bytes_recv = kwargs.get("bytes_recv", 0)
    m.packets_sent = kwargs.get("packets_sent", 0)
    m.packets_recv = kwargs.get("packets_recv", 0)
    m.errin = kwargs.get("errin", 0)
    m.errout = kwargs.get("errout", 0)
    m.dropin = kwargs.get("dropin", 0)
    m.dropout = kwargs.get("dropout", 0)
    return m


def test_network_collector_returns_snapshot(mocker):
    net_data = {"eth0": _mock_net_counters(bytes_sent=1000, bytes_recv=2000)}
    mocker.patch("psutil.net_io_counters", return_value=net_data)
    mocker.patch("psutil.net_if_stats", return_value={})
    mocker.patch("psutil.net_connections", return_value=[])
    mocker.patch("guardian.collector.network._parse_proc_net_snmp", return_value=(0, 0))
    mocker.patch("guardian.collector.network._dns_latency", return_value=10.0)

    collector = NetworkCollector()
    snap = collector.collect()

    assert snap.collector_name == "network"
    assert snap.status == "ok"
    assert "interfaces" in snap.metrics
    assert "tcp_connections" in snap.metrics
    assert snap.metrics["dns_latency_ms"] == 10.0


def test_network_skips_loopback(mocker):
    net_data = {
        "lo": _mock_net_counters(),
        "eth0": _mock_net_counters(),
    }
    mocker.patch("psutil.net_io_counters", return_value=net_data)
    mocker.patch("psutil.net_if_stats", return_value={})
    mocker.patch("psutil.net_connections", return_value=[])
    mocker.patch("guardian.collector.network._parse_proc_net_snmp", return_value=(0, 0))
    mocker.patch("guardian.collector.network._dns_latency", return_value=5.0)

    collector = NetworkCollector()
    snap = collector.collect()

    assert "lo" not in snap.metrics["interfaces"]
    assert "eth0" in snap.metrics["interfaces"]
