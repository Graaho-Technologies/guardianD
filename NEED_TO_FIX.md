# NEED_TO_FIX — GuardianD false-positive & correctness audit

> **STATUS (2026-06-18): ALL 10 FIXES IMPLEMENTED ✅** — FIX-1…FIX-10 done, each with
> regression tests. Full suite: **279 passed**, only the **12 pre-existing
> `test_storage_log_writer.py` FileNotFound failures** remain (confirmed failing on a clean
> stashed tree — unrelated to this work). New config knobs added to schema + loader + both
> templates and verified to load/validate. Still TODO: deploy to the running daemon
> (`sudo pip install --no-deps --force-reinstall .` → update `/etc/guardian/guardian.yaml` →
> `systemctl restart guardian`) and verify post-warmup, then commit/push per the grouping below.

> Audit date: **2026-06-18**. Scope: every collector, the alert router's threshold
> evaluation, and the three intelligence detectors (velocity/anomaly/forecast).
> Context: this audit followed Issue 7 (velocity/anomaly false-positive storm,
> already fixed in commit `fa57250` via absolute-magnitude floors). The items below
> are the *remaining* false-positive sources plus two silent false-negatives found
> along the way.
>
> **How to use this doc (for a future Claude session):** work top-down by priority.
> Each item has the file:line, the root cause, why it pages falsely, and a concrete
> fix. Add a regression test for every fix. Deploy the same way as Issue 7
> (`sudo pip install --no-deps --force-reinstall .` → update `/etc/guardian/guardian.yaml`
> if thresholds change → `systemctl restart guardian` → verify post-warmup). The
> installed daemon runs from a *copy*, not the repo, so a repo edit alone changes
> nothing until reinstalled.

---

## Priority 0 — highest impact (stops the most paging for least change)

### FIX-1 (systemic) — No debounce + recovery-clears-on-first-dip ⇒ flapping floods  ✅ DONE
- **Status:** Fixed. `AlertConfig.breach_cycles_to_alert` (default 2) gates a breach in
  `dispatch()` until it persists N consecutive cycles; `recovery_clear_cycles` (default 2)
  gates `_check_recovery()` so an active alert clears only after staying resolved N cycles.
  Intelligence + EMERGENCY alerts bypass the debounce. Loader + both config templates carry
  the knobs. Tests: `test_breach_debounce_holds_first_cycle`,
  `test_breach_debounce_resets_on_clear`, `test_emergency_bypasses_debounce`,
  `test_recovery_hysteresis_requires_sustained_clear` (existing cooldown/escalation/recovery
  tests pin the knobs to 1 to isolate their intent).
- **Where:** `guardian/alerter/router.py:407-472` (`_check_recovery` + `dispatch`).
- **Problem:** A metric oscillating around any threshold does: breach → alert (cooldown
  300s holds) → dip → **recovery fires and removes the fingerprint from `self._active`**
  → breach again → fires *fresh* immediately. Cooldown only suppresses re-fires while the
  condition stays *continuously* true, so flapping defeats it entirely.
- **Why it matters:** This is the amplifier that turned the velocity bug into a
  10-second flood instead of one alert. It sits under EVERY threshold below.
- **Fix:**
  1. **Consecutive-breach debounce** — only raise an alert after the condition is true
     for N consecutive collection cycles (config: e.g. `alerts.breach_cycles_to_alert: 2`).
  2. **Recovery hysteresis** — clear an active alert only after the metric drops a margin
     *below* the warn threshold (e.g. `clear_margin_percent`) AND/OR stays clear for N
     cycles, not on the first dip.
- **Tests:** simulate a value oscillating across the threshold for several cycles; assert
  exactly one alert + one recovery, not a fire/recover pair per cycle.
- **Note:** Fixing this one defuses FIX-3, FIX-4, FIX-7, FIX-8 simultaneously.

### FIX-2 — EBS volume misclassified as instance-store NVMe (confirmed firing risk on this box)  ✅ DONE
- **Status:** Fixed. `_detect_disk_type` now reads `/sys/block/<dev>/device/model` (via
  `_read_block_model`) and classifies an `nvme*` device whose model contains "elastic block
  store" as `ebs` (→ 20/100 ms thresholds) instead of `nvme` (10/50 ms). Verified live on
  `nvme0n1`. Tests: `test_detect_disk_type_ebs_nvme`,
  `test_detect_disk_type_instance_store_nvme`, `test_detect_disk_type_xvd_is_ebs`.
- **Where:** `guardian/collector/disk.py:21-42` (`_detect_disk_type`).
- **Problem:** Returns `"nvme"` for any device named `nvme*`. But on EC2 the root device
  `nvme0n1` is **EBS** (verified live: `/sys/block/nvme0n1/device/model` == `"Amazon Elastic
  Block Store"`). It therefore applies the stricter instance-store thresholds
  (`disk_await_nvme_*` = 10/50 ms) instead of the EBS thresholds that exist for exactly this
  case (`disk_await_ebs_*` = 20/100 ms). EBS is network-attached and legitimately slower.
- **Why it matters:** Makes FIX-3 fire more readily. Affects ~every EBS-backed EC2 instance.
- **Fix:** Before the `nvme*` → `"nvme"` shortcut, read `/sys/block/<base>/device/model`
  (and/or `/sys/block/<base>/device/nvme/.../model`); if it contains "Elastic Block Store",
  classify as `"ebs"`. Keep rotational-flag fallback. `xvd*` already → `ebs` (correct).
- **Tests:** mock the model file → assert `ebs`; nvme instance-store model → `nvme`.

### FIX-3 — App health checks alert on a SINGLE failure (fix is half-built already)  ✅ DONE
- **Status:** Fixed. Added `AppHealthCheck.failure_threshold: int = 2`; the router now skips a
  failing check until `consecutive_failures >= failure_threshold` (the collector already tracks
  the count). Documented in both config templates. Tests:
  `test_app_health_single_failure_suppressed`, `test_app_health_sustained_failure_alerts`.
- **Where:** `guardian/alerter/router.py:381` vs `guardian/collector/app_health.py:123-126`.
- **Problem:** The router fires CRITICAL on `if not chk.get("healthy")` — one failed probe.
  A single 502/timeout during a deploy, restart, or GC pause pages you. **The collector
  already computes `consecutive_failures` per check** (`app_health.py:123-126`) — the router
  just ignores it.
- **Fix:** Add `AppHealthCheck.failure_threshold: int = 2` (or reuse a global), and in the
  router only alert when `chk.get("consecutive_failures", 0) >= failure_threshold`. Recovery
  when it returns to healthy (already tracked, count resets to 0).
- **Tests:** 1 failure → no alert; N consecutive → alert; recovery after healthy.

---

## Priority 1 — same class as the velocity bug (% / average over a tiny sample)

### FIX-4 — Disk `await_ms` averaged over very few ops  ✅ DONE
- **Status:** Fixed. Collector now exposes `total_ops` (raw op count for the interval); router
  skips latency evaluation when `total_ops < thresholds.disk_await_min_ops` (default 50). Tests:
  `test_disk_latency_suppressed_on_few_ops`, `test_disk_latency_alerts_on_sustained_load`.
- **Where:** `guardian/collector/disk.py:107` (calc), `guardian/alerter/router.py:208` (alert).
- **Problem:** `await_ms = total_time / total_ops`. On an idle disk doing 1–2 ops per
  interval, one slow I/O (a single EBS fsync at 60 ms is normal) makes the average read
  60 ms → instant CRITICAL. Identical "small denominator" math to the velocity bug.
- **Fix:** Add a **minimum-ops floor** — skip latency evaluation (or mark it low-confidence)
  unless `total_ops >= min_iops_for_latency` over the interval (config threshold, e.g. 50).
  Optionally require the breach to persist (covered by FIX-1).
- **Tests:** 1 op @ 60 ms with floor 50 → no alert; 200 ops @ 60 ms avg → alert.

### FIX-5 — Network error/drop rate = errors ÷ packets on low traffic  ✅ DONE
- **Status:** Fixed. Router skips error/drop-rate evaluation when an interface's
  `packets_sent_per_sec + packets_recv_per_sec < thresholds.network_min_pps` (default 100).
  Tests: `test_network_error_rate_suppressed_on_low_traffic`,
  `test_network_error_rate_alerts_on_real_traffic`.
- **Where:** `guardian/collector/network.py:128-129` (calc), `guardian/alerter/router.py:243-261`.
- **Problem:** `error_rate = (errin+errout)/total_packets × 100`, thresholds 0.1%/1%
  (drop 0.05%/0.5%). On an idle interface (~5 pkts/s) a single stray error = 20% → CRITICAL.
- **Fix:** Add a **minimum-packets floor** — only evaluate error/drop rate when
  `total_packets_per_sec >= min_pps` (config, e.g. 100). Below that, the percentage is noise.
- **Tests:** 1 err / 5 pkts with floor 100 → no alert; sustained 2% over high pps → alert.

---

## Priority 2 — single-sample sensitivity (normal transient conditions → alert)

### FIX-6 — Process D-state fires on `> 0`  ✅ DONE
- **Status:** Fixed. Added `disk_sleep_warn`/`disk_sleep_critical` (default 5/20); router alerts
  on the count, not `> 0`. Also moved the count out of the title into the message/metrics so the
  fingerprint is stable (previously a changing count made every cycle a new alert, defeating
  dedup/debounce). Now also benefits from FIX-1's debounce. Tests:
  `test_dstate_below_threshold_no_alert`, `test_dstate_above_threshold_alerts_with_stable_title`.
- **Where:** `guardian/alerter/router.py:283`.
- **Problem:** Any process doing blocking I/O shows "D" (uninterruptible sleep) momentarily —
  normal. Firing WARN whenever ≥1 process is briefly in D-state flaps on any busy box.
- **Fix:** Add a threshold (config `disk_sleep_warn` / `disk_sleep_critical`, e.g. 5/20) and
  require sustain (FIX-1). Don't alert on `> 0`.
- **Tests:** count below threshold → no alert; above → alert.

### FIX-7 — CPU steal / iowait / swap-out / dirty-ratio evaluated on one instantaneous sample  ✅ DONE (via FIX-1)
- **Status:** Resolved by FIX-1. These are standard threshold WARN/CRITICAL alerts, so they now
  pass through the consecutive-breach debounce in `dispatch()` — a single transient spike no
  longer pages. No per-metric change made; raise `cpu_steal_warn` for burstable instances if
  steal is chronically transient.
- **Where:** `guardian/alerter/router.py:103-115` (steal/iowait), `:150-164` (swap-out, dirty).
- **Problem:** Brief, harmless spikes (steal ≥5%, swap-out ≥10 pg/s, dirty ≥10%) cross these
  on a single sample. Burstable (T-series) instances see transient steal constantly.
- **Fix:** Covered by FIX-1 (consecutive-breach debounce). No per-metric change needed once
  debounce exists; optionally raise `cpu_steal_warn` slightly for burstable instances.

---

## Priority 3 — forecast detector invents trends from noise

### FIX-8 — `TrendForecaster` extrapolates ~100 s of data into hours-ahead "will fill" alerts  ✅ DONE
- **Status:** Fixed. `_project` now requires `intelligence.forecast_min_samples` (default 30,
  floored at `_MIN_VALUES`) before fitting, and computes R² of the linear fit, skipping the
  forecast when `R² < intelligence.forecast_min_r2` (default 0.9) or the series is perfectly
  flat. R² is included in the alert metrics. Tests: `test_forecast_requires_min_samples`,
  `test_forecast_rejects_noisy_series`, `test_forecast_accepts_clean_long_ramp`.
- **Where:** `guardian/intelligence/forecast.py` (`_MIN_VALUES = 10`, `_project`).
- **Problem:**
  - Fits a line on as few as **10 samples (~100 s)** and projects *hours* ahead.
  - **No goodness-of-fit gate** — `np.polyfit` (`forecast.py:121`) returns a slope even for
    pure noise, so any slight positive drift → a disk/memory-full forecast.
  - Disk/memory usage is **non-monotonic** (logs rotate, caches grow/shrink), so short-window
    linear extrapolation routinely invents trends.
  - **Live risk:** root `/` is at **80%** now, so a transient write bump projects straight
    through `disk_critical` → false "disk will fill in Xh".
- **Fix:**
  1. Require a much longer minimum history before forecasting (e.g. `_MIN_VALUES` ≥ 30–60 of
     real spacing; consider sampling at coarser cadence for the trend).
  2. Add an **R² / fit-quality gate** — skip the forecast unless the linear fit explains
     enough variance (e.g. R² ≥ 0.9) and the slope is meaningfully positive.
  3. Optionally require the projected ETA to be stable across consecutive cycles.
- **Tests:** noisy flat series → no alert; clean steady ramp → alert with correct ETA.

---

## Priority 4 — NOT false-positives, but silent false-NEGATIVES (the alert can never fire)

### FIX-9 — DNS health check resolves an IP literal, never tests DNS  ✅ DONE
- **Status:** Fixed. Default `dns_check_host` changed from the IP literal `169.254.169.253` to
  the hostname `amazonaws.com` (collector + schema + both templates); `getaddrinfo` now performs
  a real query via the system resolver (the VPC resolver on EC2), so the "DNS Resolution Failing"
  alert can actually fire. Fallback changed `8.8.8.8`→`1.1.1.1` (informational only). Tests:
  `test_dns_latency_unhealthy_on_failure`, `test_dns_latency_healthy_on_success`,
  `test_dns_default_host_is_a_hostname`.
- **Where:** `guardian/collector/network.py:81-90` (`_dns_latency`), default host
  `169.254.169.253` (config `collector.dns_check_host`).
- **Problem:** `socket.getaddrinfo("169.254.169.253", None)` on an IP literal does **no DNS
  query** — it just parses the IP and returns instantly, so `dns_healthy` is always True. The
  "DNS Resolution Failing" CRITICAL (`router.py:263`) can never fire on a real DNS outage.
- **Fix:** Resolve a **hostname** (e.g. `amazonaws.com` or a configurable FQDN), optionally
  *via* the configured resolver. Keep the timeout. Add a real latency threshold if desired.
- **Tests:** mock getaddrinfo raising → unhealthy; success → healthy with latency.

### FIX-10 — dmesg severity always parsed as "warn"; kernel-critical alert is dead  ✅ DONE
- **Status:** Fixed. Collector now runs `dmesg --raw` (emits `<PRI>[boot_secs] message`) and
  parses the real syslog level via `PRI & 7` → `_LEVEL_MAP`, keeping only warn-and-above lines
  (`_ALERT_LEVELS`). Boot-relative `[secs]` timestamps are converted with `boot_time`. The
  "Kernel Critical Event" alert can now actually fire on crit/alert/emerg. Tests:
  `test_dmesg_raw_priority_parsed_to_level` (+ existing dedup tests updated to raw format).
- **Where:** `guardian/collector/system_events.py:89-118`.
- **Problem:** `dmesg --time-format=iso` output has **no `<N>` priority prefix**, so the
  `re.match(r"<(\d+)>", message)` never matches and every line defaults to `level="warn"`.
  The router only alerts on `crit/alert/emerg` (`router.py:321`), so "Kernel Critical Event
  Detected" effectively never fires.
- **Fix:** Use `dmesg --raw` (or `-r`, or `--decode`) to get real priorities, OR switch to
  `journalctl -k -p err --since <last>`. Map the priority correctly. Keep the first-collection
  seeding (`_seen_dmesg`) so old messages don't re-alert on startup.
- **Tests:** feed a raw crit line → classified `crit` → alert; warn line → no alert.

---

## Suggested commit grouping (mirror the Issue 7 workflow)

1. **`fix(alerts): debounce + recovery hysteresis to stop threshold flapping`** — FIX-1
   (highest impact; defuses most of the rest).
2. **`fix(collectors): minimum-sample floors for disk await + network rates`** — FIX-4, FIX-5
   (same absolute-floor pattern as the velocity fix in `fa57250`).
3. **`fix(disk+app_health): EBS detection + consecutive-failure gate`** — FIX-2, FIX-3, FIX-6.
4. **`fix(forecast): require longer history + R² gate before projecting`** — FIX-8.
5. **`fix(observability): real DNS hostname check + raw dmesg priorities`** — FIX-9, FIX-10.

Each commit: add tests, run the full suite (note: `tests/test_storage_log_writer.py` has **12
pre-existing failures unrelated to any of this** — they fail on clean `main` too), then deploy
+ verify post-warmup as in `INSTALL_NOTES.md` Issue 7.

## Config knobs to add (all with safe defaults; document in `guardian.example.yaml` + loader template)
- `alerts.breach_cycles_to_alert` (debounce, default 2)
- `alerts.recovery_clear_margin` and/or `alerts.recovery_clear_cycles` (hysteresis)
- `thresholds.disk_await_min_iops` (latency sample floor, e.g. 50)
- `thresholds.network_min_pps` (error/drop-rate sample floor, e.g. 100)
- `thresholds.disk_sleep_warn` / `disk_sleep_critical` (D-state, e.g. 5/20)
- `app_health` `failure_threshold` (consecutive failures, e.g. 2)
- `intelligence.forecast_min_samples` and `forecast_min_r2` (e.g. 30 / 0.9)
