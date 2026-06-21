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

### Issue 6 (FIXED) — `setup openai` leaked the key when it couldn't write a root-owned config
Running `guardianctl setup openai` against the root-owned `/etc/guardian/guardian.yaml` *without* sudo verified the key, then failed to write and **printed the raw API key** in the "add manually" fallback. Fixed: (1) fail fast with a `sudo` hint *before* prompting for the key if the config isn't writable; (2) on a later `PermissionError`/write failure, never echo the key — point at sudo or the `GUARDIAN_OPENAI_API_KEY` env var instead. Real key that got printed during this incident should be rotated.

### Switched AI key to env-file (out of the config) + relaxed validation
After the key exposure, moved the AI key out of `guardian.yaml` into `/etc/guardian/guardian.env` (root:root, 600), which the systemd unit already loads via `EnvironmentFile=-`. Set `ai.api_key: ""` in the yaml (key now comes from `GUARDIAN_OPENAI_API_KEY`). Relaxed `validate_config`: `ai.enabled` without a key is **no longer a hard error** — AI is optional and must never block daemon startup (the enricher logs a warning and stays disabled; alerts still send). Verified: daemon restarts clean with `ai.enabled=true` and no key yet (AI off). User adds the new key to the env file, then `systemctl restart guardian` turns AI on.

### Setup wizard: `guardianctl setup openai`
Mirrors `setup telegram`. Prompts for the API key (hidden input), base URL, model, and min-severity; **verifies with a real test chat-completion**; on success writes the `ai:` block and `chmod 600`s the config (and reminds about the env-var option); on 401/other error it reports clearly and writes nothing. Tests in `tests/test_cli.py` (happy path + rejected key). Live-smoke against real OpenAI with a bogus key → clean "Key rejected (401)", config untouched.

### Issue 7 (FIXED) — intelligence layer flooded CRITICALs: velocity/anomaly fired on %-of-a-tiny-baseline (2026-06-18)

**Symptom**
- A paged `CRITICAL — Rapid Disk IOPS Increase` (6.80 → 30.78, +352.8%). Investigation showed it was one of a *continuous flood*: `guardian.jsonl` had a fresh batch every 10s — Disk IOPS "spiking" 1.2→16 (+1283%), 4.8→29 (+520%); CPU 2.4%→5.4%; TCP established 1→3 (+200%) — each fired CRITICAL then immediately recovered next interval. All trivial idle-box noise on a 16-vCPU / 30 GB / NVMe(EBS) instance doing essentially nothing (load ~0.7, IOPS in the single/low-double digits).

**Root cause**
- `guardian/intelligence/velocity.py`: alerted purely on `pct_change = (cur-prev)/prev*100` vs `velocity_spike_*_pct` (40/70), with the only guard being `prev < 1.0`. Percentage-of-a-tiny-baseline is mathematically guaranteed to explode — 1.2→16 IOPS is a *real* +1283% but only +15 IOPS, which is nothing. A 10s sampling window on an idle disk is inherently bursty (one log flush ⇒ a "spike"), so consecutive-interval % comparison floods by design.
- Same latent bug in `guardian/intelligence/anomaly.py`: a z-score on a low-variance idle metric (CPU bouncing 3%→9%) yields z≈6 ⇒ a "CRITICAL anomaly" for a 6-point blip, with no absolute-magnitude guard.

**Fix — absolute-magnitude floors (a spike must clear BOTH the % threshold AND a raw-delta floor)**
- `config/schema.py`: two new per-metric-path `Dict[str,float]` threshold fields with server-tuned defaults — `velocity_min_abs_delta` (IOPS 500, CPU 15 pts, mem 10 pts, swap-out 50 pg/s, TCP est 100) and `anomaly_min_abs_dev` (CPU/iowait 15, steal 5, mem 10, swap-out 50, DNS 50 ms). `0.0` = no floor (back-compat for unlisted metrics).
- `velocity.py`: after the positive-change check, skip when `(current-prev) < velocity_min_abs_delta[path]`; also surface `abs_delta`/`min_abs_delta` in the alert metrics for debuggability.
- `anomaly.py`: after `z>0`, skip when `(value-mean) < anomaly_min_abs_dev[path]`.
- `loader.py` (embedded template) + `config/guardian.example.yaml`: documented both blocks. Loader needed no logic change — `_merge` carries dict threshold fields through; `validate_config`'s warn<crit pairs are untouched.
- Tests: `tests/test_intelligence_velocity.py` (+3 — idle IOPS 1.2→16 and TCP 1→3 suppressed; genuine 1000→2000 IOPS still CRITICAL) and `tests/test_intelligence_anomaly.py` (+1 — mean 3/σ 1, value 9 ⇒ z=6 suppressed by the 15-pt floor). Existing anomaly tests unaffected (their deviations are ≥25, above the floor).

**Verified**
- Intelligence + config suites green (`28 passed`); full suite adds no new failures (the only 12, `tests/test_storage_log_writer.py`, pre-exist on clean `main` — see note above).
- Deployed to the live daemon: `sudo pip install --no-deps --force-reinstall .` (the service runs from the *installed* copy, not the repo), added the floor blocks to `/etc/guardian/guardian.yaml` (backup saved; `load_config` re-validated OK), `systemctl restart guardian` → active, NRestarts=0.
- **Live proof:** after the 5-min intelligence warmup, a 6-min observation window with real IOPS churn (reads 72,980→98,908, writes 13,111→29,457) produced **zero** velocity/anomaly alerts — down from one CRITICAL every 10s. Genuine threshold/forecast alerting paths unchanged.

**Operator note (separate, real):** root `/` is at **80%** (116G/146G). That is *not* noise — the disk-fill forecast may legitimately warn as it climbs; worth a cleanup pass.

## Repo changes made (commit candidates)
- `setup.cfg` — now the complete authoritative metadata (Issue 1).
- `pyproject.toml` — removed split-brain `[project]` table (Issue 1).
- `systemd/guardian.service` — moved StartLimit keys to `[Unit]` (Issue 3).
- `guardian/config/schema.py`, `guardian/intelligence/velocity.py`, `guardian/intelligence/anomaly.py`, `guardian/config/loader.py`, `config/guardian.example.yaml` + velocity/anomaly tests — absolute-magnitude floors to stop the intelligence false-positive storm (Issue 7).

## AWS account identity on alerts (2026-06-21)

**Feature** — every alert now carries **AWS Account Name** and a **redacted AWS Account ID**
alongside Instance and Environment, across all channels (Slack, Telegram, Email, Webhook),
the jsonl alert log, the REST `/status` endpoint, and Prometheus metric labels.
See `NEED_TO_FIX.md` → *FEAT-1* for the full code map.

**Install/operator steps**
- **Account ID**: nothing to do on EC2 — it auto-detects from the IMDS instance-identity
  document (`/latest/dynamic/instance-identity/document` → `accountId`). It is always shown
  **redacted** `starting****ending` (first 4 + `****` + last 4); the full 12-digit value never
  appears anywhere. Override (or set off-EC2) with `aws_account_id:` / `GUARDIAN_AWS_ACCOUNT_ID`.
- **Account name**: IMDS does **not** expose the account alias, so set `aws_account_name:` in
  `/etc/guardian/guardian.yaml` (or `GUARDIAN_AWS_ACCOUNT_NAME`). Documented in
  `README.md` §"Set the basics", the env-var table, `config/guardian.example.yaml`, and
  `config/guardian.env.example`.

**Deployed to the live daemon**
- Set `aws_account_name: "AlgoRec"` in `/etc/guardian/guardian.yaml` (backup saved as
  `guardian.yaml.bak-20260621-183938`; `load_config` re-validated OK), left `aws_account_id`
  unset for auto-detect, then `sudo pip install --no-deps --force-reinstall .` + `systemctl
  restart guardian` → active, zero errors.
- **Live proof:** `GET /api/v1/status` returns `aws_account_name: "AlgoRec"` and
  `aws_account_id: "3814****3322"` (real account auto-detected, middle redacted). A WARN
  `test-alert` to Telegram delivered (`"telegram": "sent"`) and renders both lines.
- Suite: **287 passed**; the same 12 `tests/test_storage_log_writer.py` failures remain
  (pre-existing, unrelated). Committed `b089650`, pushed to `origin/main`.
