from __future__ import annotations

from unittest.mock import MagicMock, patch

from guardian.collector.disk import DiskCollector


def _mock_partition(dev="/dev/sda1", mp="/", fstype="ext4"):
    p = MagicMock()
    p.device = dev
    p.mountpoint = mp
    p.fstype = fstype
    return p


def _mock_usage(pct=45.0):
    u = MagicMock()
    u.total = 100 * 1024**3
    u.used = int(pct / 100 * u.total)
    u.free = u.total - u.used
    u.percent = pct
    return u


def test_disk_collector_mounts(mocker):
    mocker.patch("psutil.disk_partitions", return_value=[_mock_partition()])
    mocker.patch("psutil.disk_usage", return_value=_mock_usage())
    mocker.patch("psutil.disk_io_counters", return_value={})
    import os
    st = MagicMock()
    st.f_files = 1000000
    st.f_favail = 900000
    mocker.patch("os.statvfs", return_value=st)

    collector = DiskCollector()
    snap = collector.collect()

    assert snap.status == "ok"
    assert len(snap.metrics["mounts"]) == 1
    assert snap.metrics["mounts"][0]["percent_used"] == 45.0


def test_disk_skips_pseudo_fs(mocker):
    partitions = [
        _mock_partition(dev="tmpfs", mp="/tmp", fstype="tmpfs"),
        _mock_partition(dev="/dev/sda1", mp="/", fstype="ext4"),
    ]
    mocker.patch("psutil.disk_partitions", return_value=partitions)
    mocker.patch("psutil.disk_usage", return_value=_mock_usage())
    mocker.patch("psutil.disk_io_counters", return_value={})
    import os
    st = MagicMock()
    st.f_files = 1000000
    st.f_favail = 900000
    mocker.patch("os.statvfs", return_value=st)

    collector = DiskCollector()
    snap = collector.collect()

    assert all(m["fstype"] != "tmpfs" for m in snap.metrics["mounts"])


def test_detect_disk_type_ebs_nvme(mocker):
    # EC2 root: nvme device whose model reads "Amazon Elastic Block Store"
    from guardian.collector import disk as disk_mod
    mocker.patch.object(disk_mod, "_read_block_model", return_value="Amazon Elastic Block Store")
    assert disk_mod._detect_disk_type("nvme0n1") == "ebs"


def test_detect_disk_type_instance_store_nvme(mocker):
    from guardian.collector import disk as disk_mod
    mocker.patch.object(disk_mod, "_read_block_model", return_value="Amazon EC2 NVMe Instance Storage")
    assert disk_mod._detect_disk_type("nvme1n1") == "nvme"


def test_detect_disk_type_xvd_is_ebs():
    from guardian.collector import disk as disk_mod
    assert disk_mod._detect_disk_type("xvda") == "ebs"
