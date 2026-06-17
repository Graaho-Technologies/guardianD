from __future__ import annotations

import time
from unittest.mock import MagicMock

from guardian.alerter.ai import AIEnricher
from guardian.alerter.base import Alert, AlertSeverity, make_fingerprint
from guardian.config.schema import AIConfig


def _alert(severity=AlertSeverity.CRITICAL, title="Critical CPU Usage", is_recovery=False):
    return Alert(
        id="x", severity=severity, category="cpu", title=title, message="CPU at 99%",
        metrics={"cpu_percent": 99.0}, instance_id="i-1", instance_name="host",
        environment="production", timestamp=time.time(),
        fingerprint=make_fingerprint("cpu", title), is_recovery=is_recovery,
    )


_FORMATTED = (
    "MEANING: RAM is at 99%, so the kernel will soon OOM-kill processes.\n"
    "ACTIONS:\n1. Check top processes\n2. Restart the worker"
)


def _ok_response(text=_FORMATTED):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"choices": [{"message": {"content": text}}]}
    return r


def _cfg(**kw):
    base = dict(enabled=True, api_key="sk-test", min_severity="WARN")
    base.update(kw)
    return AIConfig(**base)


def test_disabled_returns_none(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post")
    enr = AIEnricher(AIConfig(enabled=False, api_key="sk-test"))
    assert enr.enrich(_alert()) is None
    post.assert_not_called()


def test_enabled_without_key_is_disabled(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post")
    enr = AIEnricher(AIConfig(enabled=True, api_key=""))
    assert enr.enabled is False
    assert enr.enrich(_alert()) is None
    post.assert_not_called()


def test_enrich_returns_meaning_and_actions(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post", return_value=_ok_response())
    enr = AIEnricher(_cfg())
    out = enr.enrich(_alert())
    assert out is not None
    assert "OOM-kill" in out.meaning and "99%" in out.meaning
    assert "Restart the worker" in out.actions
    assert "MEANING" not in out.actions  # the label is stripped out
    assert post.call_count == 1
    _, kwargs = post.call_args
    assert kwargs["json"]["model"] == "gpt-4o-mini"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_unformatted_reply_falls_back_to_actions(mocker):
    mocker.patch("guardian.alerter.ai.requests.post",
                 return_value=_ok_response("just do X and Y"))
    out = AIEnricher(_cfg()).enrich(_alert())
    assert out.meaning == "" and out.actions == "just do X and Y"


def test_below_min_severity_skipped(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post", return_value=_ok_response())
    enr = AIEnricher(_cfg(min_severity="CRITICAL"))
    assert enr.enrich(_alert(severity=AlertSeverity.WARN)) is None
    post.assert_not_called()


def test_recovery_skipped(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post", return_value=_ok_response())
    enr = AIEnricher(_cfg())
    assert enr.enrich(_alert(is_recovery=True)) is None
    post.assert_not_called()


def test_non_200_returns_none(mocker):
    r = MagicMock()
    r.status_code = 429
    r.text = "rate limited"
    mocker.patch("guardian.alerter.ai.requests.post", return_value=r)
    assert AIEnricher(_cfg()).enrich(_alert()) is None


def test_network_error_returns_none(mocker):
    mocker.patch("guardian.alerter.ai.requests.post", side_effect=Exception("boom"))
    assert AIEnricher(_cfg()).enrich(_alert()) is None


def test_cache_avoids_second_call(mocker):
    post = mocker.patch("guardian.alerter.ai.requests.post", return_value=_ok_response())
    enr = AIEnricher(_cfg())
    a1, a2 = _alert(), _alert()  # same fingerprint
    assert enr.enrich(a1)
    assert enr.enrich(a2)
    assert post.call_count == 1  # second served from cache


def test_enrich_batch_sets_meaning_and_suggestion(mocker):
    mocker.patch("guardian.alerter.ai.requests.post", return_value=_ok_response())
    enr = AIEnricher(_cfg())
    alerts = [_alert(title="Critical CPU Usage"), _alert(title="Critical Memory Usage")]
    enr.enrich_batch(alerts)
    assert all(a.ai_meaning for a in alerts)
    assert all(a.ai_suggestion for a in alerts)
