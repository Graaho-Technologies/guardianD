# Production Install Journey — Linux + systemd

Following README.md → "Production install (Linux + systemd)".

- **Host:** Ubuntu (Linux 6.8, AWS), Python 3.10.12, systemd 249, passwordless sudo as `ubuntu`.
- **Source:** existing clone at `/home/ubuntu/guardianD` (skipping the `git clone` step).
- **Date:** 2026-06-17

## Steps (README §"Production install")
1. `sudo pip install ".[full]"` — install package system-wide.
2. Create dirs + config: `/etc/guardian`, `/var/log/guardian`, `/var/lib/guardian`; `guardianctl init`.
3. Install systemd unit, `daemon-reload`, `enable --now`, verify with `systemctl status` + `guardianctl status`.

## Issues found & fixes

### Issue 1 (BLOCKER) — `pip install` produces an unusable package: no deps, no extras, no `guardiand`/`guardianctl` commands

**Symptom**
- `sudo pip install ".[full]"` printed `WARNING: guardiand 0.1.0 does not provide the extra 'full'`.
- Installed wheel metadata had `Requires-Dist: None`, `Provides-Extra: None`, and **no console-script entry points** → `which guardiand guardianctl` returns nothing. numpy / prometheus-client never installed.

**Diagnosis (evidence-based)**
- Dumped the freshly built wheel's `METADATA`: `Metadata-Version: 2.1`, `Summary: UNKNOWN`, `License: UNKNOWN`, no `Requires-Dist`, no `Provides-Extra`. That is the fingerprint of **legacy setuptools (<61)** that does not understand PEP 621 `[project]`.
- The box ships **setuptools 59.6.0** (system) + pip 22.0.2. `pyproject.toml` declares `build-system.requires = ["setuptools>=65"]`, and pip's build isolation *did* download setuptools 82 — but the actual `bdist_wheel` step still ran under 59.6.0 (build isolation is effectively broken on this Debian/Ubuntu host). Verified by building with `setup.cfg` removed → wheel came out as `UNKNOWN-0.0.0`, proving `[project]` is never read.
- Net: all real metadata (dependencies, optional-dependencies, `[project.scripts]`) lived **only** in pyproject's `[project]`, which the building setuptools ignored. The only surviving metadata (name, version) came from `setup.cfg [metadata]`, which declared nothing else.

**Fix**
- Made `setup.cfg` the complete, authoritative declarative metadata source (works on setuptools ≥30.3, independent of build isolation): `install_requires`, `[options.extras_require]` (`intelligence`/`prometheus`/`full`/`dev`), `[options.entry_points]` console scripts, and `[options.packages.find]`.
- Removed the split-brain `[project]` table (and `[tool.setuptools.packages.find]`) from `pyproject.toml`, leaving `[build-system]` + tool configs. Single source of truth; no old-vs-new-setuptools conflict.

**Verified**
- `sudo pip install ".[full]"` → no warning, installs psutil/requests/PyYAML/click/rich/tabulate/python-dateutil + numpy + prometheus-client.
- `Provides-Extra: ['dev','full','intelligence','prometheus']`; console scripts present at `/usr/local/bin/guardiand` and `/usr/local/bin/guardianctl`.
- `guardiand --version` → `GuardianD 0.1.0`; `guardianctl --help` works; `import prometheus_client`/`numpy` OK.

### Issue 2 (minor) — `sudo pip install .` leaves root-owned `build/` + `*.egg-info` in the source tree
Building from the cloned source as root writes root-owned `build/` and `guardiand.egg-info/` into the repo, which then block later non-root builds (`Permission denied: build/lib/...`). Cleaned with `sudo rm -rf build *.egg-info`. Not a code defect; worth knowing when iterating. (Already git-ignored, so not committed.)

### Issue 3 (minor) — systemd unit put `StartLimitIntervalSec`/`StartLimitBurst` in `[Service]`

**Symptom**
```
guardian.service:16: Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring.
```
These two keys are `[Unit]`-section directives. In `[Service]` systemd silently ignores them, so the crash-loop start-rate-limit (`Restart=always` guard) never actually applied.

**Fix**
Moved `StartLimitIntervalSec=60` + `StartLimitBurst=10` into the `[Unit]` section of `systemd/guardian.service`. After reinstall: warning gone, `systemctl show guardian` reports `StartLimitIntervalUSec=1min`, `StartLimitBurst=10`.

---

## Final outcome — production install COMPLETE ✅

- `sudo pip install ".[full]"` installs cleanly with all deps + extras + console scripts.
- `/etc/guardian/guardian.yaml` generated; `instance_name` set to host; one channel enabled; `config validate` → valid.
- systemd unit installed, `enable --now`, **active / NRestarts=0 / enabled on boot**.
- `guardianctl status` → Running, all 7 collectors `ok`; EC2 collector detected real instance `i-0cbf6cf55deb860fd`.
- REST API on `127.0.0.1:9731`; heartbeat + PID in `/run/guardian`; SQLite (WAL) in `/var/lib/guardian`; logs in `/var/log/guardian`.
- `guardianctl metrics`, `alerts`, `config reload` (SIGHUP) all work.

### Action still required by the operator
- **Real alert credentials.** Telegram is enabled with placeholder `bot_token`/`chat_id` purely so the daemon could be validated and started. Replace them (`guardianctl setup telegram`, or edit `/etc/guardian/guardian.yaml` + `sudo systemctl restart guardian`) — until then, alert delivery fails gracefully (daemon does not crash; a startup "Critical CPU Usage" alert was logged and its send failed harmlessly).

## Telegram channel — real credentials wired in (2026-06-17)
- Bot `@graaho_bot`, group **"(Graaho) System Monitoring"** chat_id `-4832975455` (new-style basic group; the `#-4832975455` from the Telegram web URL is used as-is).
- Verified token+chat with a direct `getMe`/`sendMessage` API call before writing them in.
- Wrote real values into `/etc/guardian/guardian.yaml`, **`chmod 600`** it (now holds a secret; root-owned, systemd runs as root so still readable), `config validate` → valid, restarted service.
- Confirmed end-to-end: `guardianctl test-alert --channel telegram --severity WARN` delivered with no alerter error in the journal.

### Issue 4 (FIXED) — `test-alert` couldn't distinguish delivered / skipped / failed / not-enabled
**What it really was:** `alerter.send()` returns `False` for three different reasons — channel disabled, alert below the channel's `min_severity`, or an actual send failure — and the endpoint collapsed all of them (plus "no such channel") into one `sent` boolean. Result: `test-alert --channel telegram --severity INFO` (INFO < telegram's WARN min) reported a misleading "failed to send", and `--channel all` hid partial failures behind `any()`.

**Fix**
- `guardian/exposition/rest_api.py` `_handle_test_alert`: returns `results: {channel: outcome}` where outcome ∈ `sent | failed | skipped_below_severity | not_enabled`, alongside the existing `sent` boolean (kept for back-compat).
- `guardianctl/cli.py` `test-alert`: renders each channel's outcome (✓ sent / ✗ failed / – skipped / – not enabled) and exits non-zero when nothing was delivered or any channel failed.
- `tests/test_cli.py`: added 4 tests for the sent / failed / skipped / not-enabled paths.

**Verified on the live daemon** (after `sudo pip install . && systemctl restart guardian`):
- WARN→telegram → `✓ sent` (exit 0); INFO→telegram → `– skipped (below min_severity)` (exit 1, no longer "failed"); slack (disabled) → `– not enabled` (exit 1); all@CRITICAL → `telegram: ✓ sent` (exit 0).

> Pre-existing unrelated failures: `tests/test_storage_log_writer.py` (12) fail on clean `main` too (FileNotFoundError on `guardian.jsonl`) — not touched here.

## Metric-coverage audit + README + intuitive Telegram alerts (2026-06-17)
**Coverage audit:** read every collector. Coverage is comprehensive for a single-host daemon — CPU (incl. steal/iowait/ctx-switches/freq), memory (swap rates, OOM, dirty pages, hugepages, file descriptors), disk (per-mount usage+inodes, per-disk latency/util/IOPS with NVMe/EBS/SSD/HDD detection), network (full TCP state machine, retransmits/resets, sockstat, DNS latency), processes (states, zombies, D-state, top-N), EC2 (IMDSv2 metadata, spot notice, IAM, EBS, lifecycle), system events (dmesg, OOM, failed units, PSI pressure stalls), app-health (http/port/process/systemd). **No collectors were missing** — no new collection added.

**README:** added a "What GuardianD watches" section (plain-English table of every area + the alerts it raises + how alerts reach you) and a sample rendered Telegram alert.

**Intuitive Telegram alerts (Issue 5 / enhancement):** alert *titles* were already descriptive but *messages* were terse numbers. Added `guardian/alerter/explain.py` (`impact_hint` + `severity_meaning`) — a channel-agnostic, plain-English "why it matters" mapping keyed on alert title with a category fallback. Telegram messages now include `💡 What this means:` and `🎚 Severity:` lines. Tests in `tests/test_alerter_explain.py`. Verified live: realistic CPU-steal example delivered to the group with the new lines. Full suite: 241 passed (same 12 pre-existing log_writer failures).

## AI-assisted alert enrichment, all channels (2026-06-17)
Added an optional AI layer that interprets each alert and suggests concrete quick-fix steps, rendered on **all** channels.
- `guardian/alerter/ai.py` (new): `AIEnricher` calls an OpenAI-compatible chat API via `requests` (no SDK — keeps deps minimal). Off by default; never raises; network timeout; severity-gated; per-fingerprint TTL cache so repeating alerts don't re-spend tokens; parallel `enrich_batch`.
- `schema.py`: new `AIConfig` (`enabled/provider/api_key/base_url/model/timeout/max_tokens/include_metrics/min_severity/cache_ttl`), added `ai` to `GuardianConfig`. `base.py`: `Alert.ai_suggestion` field.
- `router.py`: enriches each batch once before dispatch (shared across channels).
- Rendering: telegram (`🤖 AI suggestion`), slack (section block), email (HTML box), webhook (`ai_suggestion` JSON field).
- `loader.py`: parse `ai`, env override `GUARDIAN_OPENAI_API_KEY` → fallback `OPENAI_API_KEY`, validation (enabled requires key), `ai:` block in generated config. `rest_api.py`: `api_key` added to redaction set.
- Tests: `tests/test_alerter_ai.py` (disabled/no-key/severity/recovery/non-200/network-error/cache/batch). Full suite 250 passed (same 12 pre-existing log_writer failures).
- **Provider note:** user asked for OpenAI specifically; implemented OpenAI Chat Completions, but `base_url` makes it work with any OpenAI-compatible endpoint (Azure, local gateway).
- **Verified:** off-by-default daemon restart healthy (NRestarts=0). Live demo: enriched Telegram alert delivered to the group using the real enrich→render→send path with a canned suggestion (no OpenAI key on this host). To run for real: set `ai.enabled: true` + `GUARDIAN_OPENAI_API_KEY`.

### Refinement — make "What this means" itself AI-driven (metric-aware)
Originally the AI only filled a separate "AI suggestion" block; the "What this means" line stayed the static generic hint (e.g. "A monitored threshold was crossed."). Restructured the enricher to return a structured `AIResult(meaning, actions)` — the model is prompted for a `MEANING:` (one/two sentences interpreting the *actual numbers*) and `ACTIONS:` (numbered fixes), parsed tolerantly (falls back to treating the whole reply as actions if the model ignores the format). New `Alert.ai_meaning` field. Channels now use AI meaning where they show interpretation:
- telegram: `💡 What this means` = `ai_meaning or impact_hint(alert)` (AI when on, static otherwise); `🤖 AI suggestion` = actions.
- slack/email: AI block shows "What this means" + "Suggested fix". webhook: `ai_meaning` + `ai_suggestion` fields.
Tests updated (structured result + unformatted-reply fallback). Verified live: a Disk-Critical alert delivered to the group with the "What this means" line referencing the real 96.2%.

### Setup wizard: `guardianctl setup openai`
Mirrors `setup telegram`. Prompts for the API key (hidden input), base URL, model, and min-severity; **verifies with a real test chat-completion**; on success writes the `ai:` block and `chmod 600`s the config (and reminds about the env-var option); on 401/other error it reports clearly and writes nothing. Tests in `tests/test_cli.py` (happy path + rejected key). Live-smoke against real OpenAI with a bogus key → clean "Key rejected (401)", config untouched.

## Repo changes made (commit candidates)
- `setup.cfg` — now the complete authoritative metadata (Issue 1).
- `pyproject.toml` — removed split-brain `[project]` table (Issue 1).
- `systemd/guardian.service` — moved StartLimit keys to `[Unit]` (Issue 3).
