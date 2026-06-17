"""AI-assisted alert enrichment.

When enabled, turns a raw alert into a short plain-English interpretation plus
2-4 concrete, immediate quick-fix steps ("check this", "run that"), shared across
every channel. Calls an OpenAI-compatible chat-completions API over plain HTTP
(no SDK — keeps dependencies minimal).

Design rules (same as the rest of the daemon):
  * Never raises — any failure returns None and the caller falls back to the
    built-in static hint.
  * Always uses a network timeout; never blocks forever.
  * Off by default; only enriches alerts at/above a configured severity.
  * Caches a suggestion per alert fingerprint so a repeating alert doesn't incur
    a fresh API call (and cost) every cycle.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, NamedTuple, Optional, Tuple

import requests

from ..config.schema import AIConfig
from ..utils.logger import get_logger
from .base import Alert, SEVERITY_ORDER

_log = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a senior Linux and AWS SRE assistant embedded in a server-monitoring "
    "daemon. You receive a single infrastructure alert with its real metric values. "
    "Reply in plain text using EXACTLY this format and nothing else:\n"
    "MEANING: <one or two sentences interpreting what is happening and why it "
    "matters, referencing the actual numbers in this alert>\n"
    "ACTIONS:\n"
    "1. <concrete immediate action or command>\n"
    "2. <...>\n"
    "Give 2-4 actions; shell commands are welcome. Be specific to THIS alert and its "
    "numbers, be terse, do not repeat the alert text verbatim, keep it under 120 words."
)


class AIResult(NamedTuple):
    meaning: str
    actions: str


def _parse_result(content: str) -> AIResult:
    """Split the model reply into (meaning, actions). Tolerant of format drift."""
    text = content.strip()
    meaning = ""
    actions = ""
    m = re.search(r"(?is)\bmeaning\b\s*:\s*(.*?)(?:\n\s*\bactions\b\s*:|\Z)", text)
    a = re.search(r"(?is)\bactions\b\s*:\s*(.*)\Z", text)
    if m:
        meaning = m.group(1).strip()
    if a:
        actions = a.group(1).strip()
    if not meaning and not actions:
        # Model ignored the format — keep the whole thing as the actionable text.
        actions = text
    return AIResult(meaning=meaning, actions=actions)


class AIEnricher:
    def __init__(self, config: AIConfig) -> None:
        self.config = config
        self.enabled = bool(config.enabled and config.api_key)
        if config.enabled and not config.api_key:
            _log.warning("ai.enabled=true but no api_key/OPENAI key set — AI enrichment disabled")
        self._min_rank = SEVERITY_ORDER.get(config.min_severity, 1)
        # fingerprint -> (result, created_ts)
        self._cache: Dict[str, Tuple[AIResult, float]] = {}
        self._lock = threading.Lock()

    # -- public API -----------------------------------------------------------

    def enrich_batch(self, alerts: List[Alert]) -> None:
        """Populate alert.ai_meaning + alert.ai_suggestion for eligible alerts, in parallel."""
        if not self.enabled:
            return
        targets = [
            a for a in alerts
            if self._eligible(a) and not (a.ai_meaning or a.ai_suggestion)
        ]
        if not targets:
            return
        workers = min(4, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self.enrich, a): a for a in targets}
            for fut in as_completed(futures):
                alert = futures[fut]
                try:
                    result = fut.result()
                except Exception:  # never let enrichment break dispatch
                    result = None
                if result:
                    alert.ai_meaning = result.meaning
                    alert.ai_suggestion = result.actions

    def enrich(self, alert: Alert) -> Optional[AIResult]:
        """Return AI (meaning, actions) for one alert, or None. Never raises."""
        if not self.enabled or not self._eligible(alert):
            return None
        cached = self._cache_get(alert.fingerprint)
        if cached is not None:
            return cached
        result = self._call_api(alert)
        if result:
            self._cache_put(alert.fingerprint, result)
        return result

    # -- internals ------------------------------------------------------------

    def _eligible(self, alert: Alert) -> bool:
        if alert.is_recovery:
            return False
        return SEVERITY_ORDER.get(alert.severity.name, 0) >= self._min_rank

    def _build_user_prompt(self, alert: Alert) -> str:
        lines = [
            f"Alert: {alert.title}",
            f"Severity: {alert.severity.name} (category: {alert.category})",
            f"Detail: {alert.message}",
            f"Host: {alert.instance_name or alert.instance_id} "
            f"(id {alert.instance_id}), environment {alert.environment}",
        ]
        if self.config.include_metrics and alert.metrics:
            kv = ", ".join(f"{k}={v}" for k, v in list(alert.metrics.items())[:10])
            lines.append(f"Triggering metrics: {kv}")
        return "\n".join(lines)

    def _call_api(self, alert: Alert) -> Optional[AIResult]:
        try:
            url = f"{self.config.base_url.rstrip('/')}/chat/completions"
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": self._build_user_prompt(alert)},
                    ],
                    "max_tokens": self.config.max_tokens,
                    "temperature": 0.2,
                },
                timeout=self.config.timeout_seconds,
            )
            if resp.status_code != 200:
                _log.error("AI enrichment failed: %s %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            content = (
                data.get("choices", [{}])[0].get("message", {}).get("content", "")
            ).strip()
            if not content:
                return None
            return _parse_result(content)
        except Exception as exc:
            _log.error("AI enrichment error: %s", exc)
            return None

    def _cache_get(self, fp: str) -> Optional[AIResult]:
        ttl = self.config.cache_ttl_seconds
        now = time.time()
        with self._lock:
            entry = self._cache.get(fp)
            if entry and (now - entry[1]) < ttl:
                return entry[0]
        return None

    def _cache_put(self, fp: str, result: AIResult) -> None:
        now = time.time()
        with self._lock:
            self._cache[fp] = (result, now)
            # bound memory
            if len(self._cache) > 500:
                cutoff = now - self.config.cache_ttl_seconds
                self._cache = {
                    k: v for k, v in self._cache.items() if v[1] >= cutoff
                } or {fp: (result, now)}
