from __future__ import annotations

import re

import pytest

from guardian.collector.system_events import SystemEventsCollector, _OOM_RE


# ─── Subprocess helpers ────────────────────────────────────────────────────────

def _mock_run(mocker, dmesg_out: str = "", systemctl_out: str = "", uname_out: str = "5.15.0"):
    """
    Patches guardian.collector.system_events.subprocess.run so that the
    specific commands return controlled output.
    """
    import subprocess as _subprocess

    def _fake_run(cmd, **kwargs):
        result = _subprocess.CompletedProcess(cmd, 0)
        result.stdout = ""
        result.stderr = ""
        if cmd[0] == "dmesg":
            result.stdout = dmesg_out
        elif cmd[0] == "systemctl":
            result.stdout = systemctl_out
        elif cmd[0] == "uname":
            result.stdout = uname_out
        return result

    return mocker.patch(
        "guardian.collector.system_events.subprocess.run",
        side_effect=_fake_run,
    )


# ─── Basic collect ─────────────────────────────────────────────────────────────

def test_system_events_returns_snapshot(mocker):
    _mock_run(mocker)
    collector = SystemEventsCollector()
    snap = collector.collect()
    assert snap.collector_name == "system_events"
    assert snap.status == "ok"
    assert isinstance(snap.metrics, dict)


def test_system_events_has_required_keys(mocker):
    _mock_run(mocker)
    collector = SystemEventsCollector()
    snap = collector.collect()
    m = snap.metrics
    for key in (
        "dmesg_errors_new",
        "oom_kill_count_new",
        "failed_unit_count",
        "kernel_version",
        "uptime_seconds",
    ):
        assert key in m, f"missing key: {key}"


def test_psi_available_or_not(mocker):
    """psi_available must be a bool — True or False depending on the kernel."""
    _mock_run(mocker)
    collector = SystemEventsCollector()
    snap = collector.collect()
    assert isinstance(snap.metrics["psi_available"], bool)


# ─── OOM regex ─────────────────────────────────────────────────────────────────

def test_oom_parse_regex_matches():
    line = (
        "Killed process 1234 (python3) oom_score_adj 0 total-vm:12345kB, "
        "anon-rss:67890kB, file-rss:0kB, shmem-rss:0kB, UID:0 "
        "pgtables:48kB oom_kill_count:1 free pages:0 free pages:0 kB"
    )
    m = _OOM_RE.search(line)
    assert m is not None, "OOM regex did not match the sample line"
    assert m.group(1) == "1234"
    assert m.group(2) == "python3"


def test_oom_parse_regex_kill_variant():
    line = "Kill process 999 (bash) total-vm:100kB, anon-rss:50kB, file-rss:0kB 200 kB"
    m = _OOM_RE.search(line)
    assert m is not None
    assert m.group(1) == "999"
    assert m.group(2) == "bash"


def test_oom_parse_regex_no_match_on_normal_line():
    line = "Normal kernel message without OOM information"
    m = _OOM_RE.search(line)
    assert m is None


# ─── dmesg deduplication ───────────────────────────────────────────────────────

def test_dmesg_dedup_first_collection_seeds_no_new_errors(mocker):
    """
    On the first collection the seen-set is seeded, so no entries should
    appear in dmesg_errors_new even when dmesg output is non-empty.
    """
    dmesg_line = "<3>[   10.123456] some kernel error occurred"
    _mock_run(mocker, dmesg_out=dmesg_line)

    collector = SystemEventsCollector()
    snap = collector.collect()

    # First collection must produce zero new errors (seeding phase)
    assert snap.metrics["dmesg_error_count_new"] == 0
    assert snap.metrics["dmesg_errors_new"] == []


def test_dmesg_dedup_second_collection_no_repeat(mocker):
    """
    If the same dmesg line appears in both the first and second collection,
    it must NOT be reported in the second collection.
    """
    dmesg_line = "<3>[   10.123456] some kernel error occurred"
    _mock_run(mocker, dmesg_out=dmesg_line)

    collector = SystemEventsCollector()
    collector.collect()        # first: seeds the set
    snap2 = collector.collect()  # second: same line — should not re-report

    assert snap2.metrics["dmesg_error_count_new"] == 0


def test_dmesg_new_entry_reported_on_second_collection(mocker):
    """
    A line that did NOT appear in the first collection SHOULD be reported
    as a new error in the second collection.
    """
    old_line = "<3>[   10.123456] old error"
    new_line = "<2>[   20.654321] brand new critical error that didnt exist before"

    call_count = {"n": 0}
    import subprocess as _subprocess

    def _fake_run(cmd, **kwargs):
        result = _subprocess.CompletedProcess(cmd, 0)
        result.stdout = ""
        result.stderr = ""
        call_count["n"] += 1
        if cmd[0] == "dmesg":
            # First call: only old_line; second call: both lines
            result.stdout = old_line if call_count["n"] <= 1 else f"{old_line}\n{new_line}"
        elif cmd[0] == "uname":
            result.stdout = "5.15.0"
        return result

    mocker.patch("guardian.collector.system_events.subprocess.run", side_effect=_fake_run)

    collector = SystemEventsCollector()
    collector.collect()        # first: seeds with old_line
    snap2 = collector.collect()  # second: new_line is fresh

    assert snap2.metrics["dmesg_error_count_new"] == 1
    assert snap2.metrics["dmesg_errors_new"][0]["message"] != ""


def test_dmesg_raw_priority_parsed_to_level(mocker):
    """FIX-10: a <2> crit line must be classified 'crit', a <6> info line dropped."""
    crit_line = "<2>[   42.000000] critical kernel failure"
    info_line = "<6>[   43.000000] just informational"

    call_count = {"n": 0}
    import subprocess as _subprocess

    def _fake_run(cmd, **kwargs):
        result = _subprocess.CompletedProcess(cmd, 0)
        result.stdout = ""
        result.stderr = ""
        call_count["n"] += 1
        if cmd[0] == "dmesg":
            # seed empty first, then emit both lines on the second collection
            result.stdout = "" if call_count["n"] <= 1 else f"{crit_line}\n{info_line}"
        elif cmd[0] == "uname":
            result.stdout = "5.15.0"
        return result

    mocker.patch("guardian.collector.system_events.subprocess.run", side_effect=_fake_run)

    collector = SystemEventsCollector()
    collector.collect()         # seed
    snap2 = collector.collect()

    new = snap2.metrics["dmesg_errors_new"]
    crit = [e for e in new if e["level"] == "crit"]
    assert len(crit) == 1
    assert crit[0]["message"] == "critical kernel failure"
    # info (<6>) is below warn → dropped entirely
    assert all(e["level"] != "info" for e in new)


# ─── Systemd failed units ──────────────────────────────────────────────────────

def test_systemctl_failed_units_parsed(mocker):
    systemctl_out = "myapp.service   loaded failed failed   My Application\n"
    _mock_run(mocker, systemctl_out=systemctl_out)

    collector = SystemEventsCollector()
    snap = collector.collect()

    assert snap.metrics["failed_unit_count"] == 1
    unit = snap.metrics["failed_systemd_units"][0]
    assert unit["unit"] == "myapp.service"
    assert unit["active"] == "failed"


def test_systemctl_no_failed_units(mocker):
    _mock_run(mocker, systemctl_out="")

    collector = SystemEventsCollector()
    snap = collector.collect()

    assert snap.metrics["failed_unit_count"] == 0
    assert snap.metrics["failed_systemd_units"] == []


# ─── Kernel version and uptime ─────────────────────────────────────────────────

def test_kernel_version_captured(mocker):
    _mock_run(mocker, uname_out="5.15.0-91-generic\n")

    collector = SystemEventsCollector()
    snap = collector.collect()

    assert snap.metrics["kernel_version"] == "5.15.0-91-generic"


def test_uptime_seconds_positive(mocker):
    _mock_run(mocker)

    collector = SystemEventsCollector()
    snap = collector.collect()

    assert snap.metrics["uptime_seconds"] > 0
    assert snap.metrics["boot_time"] > 0
