from __future__ import annotations

import time

from guardian.alerter.base import Alert, AlertSeverity
from guardian.alerter.explain import impact_hint, severity_meaning


def _alert(category="cpu", title="High CPU Usage", severity=AlertSeverity.WARN, is_recovery=False):
    return Alert(
        id="x", severity=severity, category=category, title=title, message="",
        metrics={}, instance_id="i", instance_name="n", environment="e",
        timestamp=time.time(), fingerprint="f", is_recovery=is_recovery,
    )


def test_title_keyword_match_wins_over_category():
    # "CPU Steal" must map to the steal explanation, not the generic CPU one.
    hint = impact_hint(_alert(category="cpu", title="Severe EC2 CPU Steal — Noisy Neighbor"))
    assert "hypervisor" in hint.lower()


def test_oom_hint():
    assert "killed" in impact_hint(_alert("memory", "OOM Kill Detected")).lower()


def test_spot_hint_is_urgent():
    hint = impact_hint(_alert("ec2", "SPOT INSTANCE TERMINATION NOTICE — 2 MINUTES"))
    assert "2 minutes" in hint


def test_recovery_overrides_everything():
    hint = impact_hint(_alert("disk", "Disk Space Critical: /", is_recovery=True))
    assert "cleared" in hint.lower()


def test_unknown_title_falls_back_to_category():
    hint = impact_hint(_alert(category="network", title="Some Brand New Network Thing"))
    assert hint and "network" in hint.lower()


def test_every_severity_has_meaning():
    for sev in AlertSeverity:
        assert severity_meaning(sev)
