# Sentinel ↔ Gamma 4-arm coverage plan

**Date:** 2026-05-11 · **Status:** Plan only — implementation deferred to a follow-up commit
**Author:** Claude (read-only audit of `sentinel_agent.py`, `system_monitor.py`, `gamma/arm_state.py`, `gamma_agent.py`, `position_monitor.py`)

---

## ⚠️ Notes for the implementation session

Two follow-ups raised by the operator after plan approval. Read these BEFORE starting implementation:

### N1 — `promotion_history.jsonl` schema (pin before Commit 4)

The plan references this file in G11 but the prior draft didn't pin the shape. Use this exactly:

```json
{
  "event": "promote" | "reset",
  "arm_id": "a" | "b" | "c" | "d" | null,
  "timestamp": "<ISO-8601 with timezone>",
  "reason": "<operator-provided string captured at --confirm>"
}
```

- `arm_id` is **null for reset events** (`event="reset"`); always set for `event="promote"`
- `timestamp` is ISO-8601 with timezone, e.g. `"2026-07-10T20:31:42-04:00"`
- `reason` is the free-form string the operator passes via `--reason "..."` (already captured by `gamma_agent.py` for both subcommands)
- File: `/root/quantai-v2/shared-data/logs/promotion_history.jsonl` (append-only)
- Created lazily on first `--confirm` invocation; G11 must handle "file missing" as "no recent legitimate action → don't suppress"
- Sentinel's G11 check reads the **last line only** and checks `timestamp` is within 24h of now. No need to parse the whole file.

Tests for Commit 4 must cover both event shapes and the "file missing" path.

### N2 — G3 threshold assumption documentation

The "1 line per market day" baseline holds **only while Gamma uses the cron-driven scan model** (`30 20 * * 1-5` → `run_scan_4arm` called once per weekday). If the architecture ever moves to event-driven scans (WebSocket triggers, multi-timeframe scans, intraday rescans, etc.), the growth threshold (≥2/day=warn, ≥5/day=crit) will start producing false positives.

**Required in Commit 1** — directly above the G3 check in `system_monitor.py`, add a comment:

```python
# G3: threshold assumes single daily scan cron (30 20 * * 1-5).
# Baseline = 1 line/market day in gamma_ranking_decisions.jsonl.
# Recalibrate the growth thresholds (currently >=2 warn, >=5 crit)
# if scan frequency changes — e.g. event-driven scans, multi-timeframe
# triggers, intraday rescans. The stagnation check (0 entries on a
# market day after 21:00 UTC) remains valid regardless of cadence.
```

This is the kind of implicit assumption that bites future maintainers. The comment is the audit trail.

---

## Step 1a — What Sentinel monitors today

Sentinel is an LLM-driven (Sonnet for apply, Haiku for observe) operations agent that wakes on a fixed ET schedule and consumes a deterministic context bundle assembled by `gather_context()` (`sentinel_agent.py:576`). It produces a structured plan via Claude with `findings` + `proposals` (fix-class = `safe_auto` / `propose_wait` / `never_touch`), gated through hardcoded Python safety rails before any LLM output can mutate the system.

**Context bundle inputs**:
1. `system-health-report.json` (14 deterministic checks from `system_monitor.py`)
2. `errors.db` — top 8 unresolved events by count + severity histogram (`query_errors_db_summary`, line 546)
3. `quantai-test-results.json` — pytest pass/fail counts + last-run timestamp
4. `weekly_reports/*.md` mtime — age in days
5. `docs/error-catalog.json` — taxonomy with auto-action hints
6. Open positions count (read from `quantai-positions.json`)
7. Trading window flag (13:00–20:00 UTC weekday)

**System-health checks** (`system_monitor.py:479`, the `CHECKS` table):
| # | Check | What it watches |
|---|---|---|
| 1 | `ibkr_port` | TCP 4002 reachable |
| 2 | `litellm_4000` | LLM proxy port |
| 3 | `clawroute_18790` | LLM ingress |
| 4 | `cron_freshness` | Reads `cron-status.json`. **Already covers `gamma_agent.py --scan` and `--execute` staleness** (entries in `collect_cron.py:41-42`) |
| 5 | `disk` / `memory` | OS-level |
| 6 | `self_learning_sla` | Diagnosis + review files within SLA after every CLOSED journal entry |
| 7 | `weekly_synthesis` | Friday report present by Fri 22:00 UTC |
| 8 | `collector_staleness` | `/var/dashboard/state/*.json` not stale |
| 9 | `journal_schema` | Last `trades.jsonl` line parses; has `status`/`trade_id`/`legs` (line 365) |
| 10 | `test_results` | Test suite freshness + pass rate |
| 11 | `dashboard_html_size` | Catches truncated index.html deploys |
| 12 | `graphify` | Graph rebuild freshness |
| 13 | `open_positions` | Compares broker vs journal (ghost detection upstream of Sentinel — see §1b) |

**Schedule** (`sentinel_agent.py:151`):
- Weekday 08:30 ET → `apply` (pre-market mutation slot)
- Weekday 10:00 / 11:00 / 12:00 / 13:00 / 14:00 / 15:00 ET → `observe` (read-only diagnose, mid-trading)
- Weekday 16:15 ET → `apply` (post-close, with end-of-day digest drain)
- Weekend & off-hours → exits silently

**Discord channels** (env var names; sentinel never reads values):
- `DISCORD_CHANNEL_SYSTEM_HEALTH` (`CH_HEALTH`) — digests, observe summaries
- `DISCORD_CHANNEL_APPROVALS` (`CH_APPROVALS`) — `propose_wait` cards needing ✅/❌
- `DISCORD_CHANNEL_LOGS` (`CH_LOGS`) — informational ops events (e.g., "reclassified 5 noise events")
- `DISCORD_CHANNEL_ALERTS` (`CH_FALLBACK`) — only used if all others missing

**Hardcoded safety rails** (Python-enforced, not LLM-overrideable):
- `NEVER_MODIFY_PATHS` — trading-path scripts + Gamma 4-arm internals (commit 5 added `gamma/rankers/`, `gamma/arm_state.py`, `gamma/reward_risk_estimator.py`, `gamma/promotion_evaluator.py`)
- `NEVER_TOUCH_PATHS` — `/etc/systemd/`, both `.env` files, `/home/openclaw/`, journal directory
- `NEVER_RESTART_SERVICES_BLANKET` — `openclaw` never
- `POSITION_GATED_SERVICES` — `ibgateway` only off-hours with 0 positions
- `CREDENTIAL_PATTERNS` — regex matching `secret|token|api_key|password` rejects the proposal
- `MAX_FILE_MUTATIONS_PER_RUN = 3`, `MAX_DIFF_LINES = 80`, `ATTEMPT_BUDGET = 3`, `APPROVAL_EXPIRY_HOURS = 48`

**Built-in deterministic action** (`reclassify_catalog_noise`, line 494): every `apply` cycle, SQL `UPDATE` on `errors.db` reclassifies known-noise patterns (fail2ban, UFW, SSH brute force, health-monitor stale-socket) from warning→info. No LLM involved.

---

## Step 1b — Coverage that already lands transparently on Gamma 4-arm

| Failure mode | Already covered by | Why it works |
|---|---|---|
| Gamma scan cron skipped | `check_cron_freshness` | `collect_cron.py:41-42` already registers both `gamma_agent.py --scan` (4:30 PM ET) and `--execute` (9:33 AM ET) with `interval=86400s`; status flips to `error` during market hours and `warning` off-hours |
| Trade journal corruption (union `trades.jsonl`) | `check_journal_schema` | Reads last line, validates `status` + `trade_id|id` + `legs`. Catches truncated writes from any agent — but **does NOT validate per-arm journals (`gamma_arm_<id>_trades.jsonl`)** |
| Broker disconnect / IBKR down | `check_ibkr_port` | TCP 4002 reachability |
| Ghost positions (broker has position with no journal entry) | `position_monitor.reconcile_ghost_positions` (line 908) + `_GHOST_ALERT_FILE` cooldown | Already posts 🔴 Discord with 60-min cooldown per symbol. Symmetric: also detects "journal lies" (journal has open trade but broker doesn't). **This is per-trade-id and doesn't know about arm partitioning** |
| Per-arm reconciliation drift | `gamma/arm_state.reconcile_and_alert` (line 462) | Per-arm equity invariants: `expected_equity == starting + cumulative_realized`, and `cash + open_max_risk == current_equity`. **Already alerts via `_post_discord` injected at call sites**. Threshold $1.00. |
| Partial-broker-failure cancel-all | `gamma_agent.py:984` | Already posts `🔴 Gamma 4-arm \| {sym} cancelled — partial broker failure` to `_post_discord`. **Event is logged but Sentinel doesn't aggregate frequency**. |
| Reflection backlog escalation | `reflection_reconciler.py:170` | Nightly 22:00 UTC; on escalation to `manual_review` posts ⚠️ to `DISCORD_CHANNEL_ALERTS`. **Sentinel doesn't see this until errors.db catches the Discord post (it usually doesn't)** |
| Test failures impacting gamma | `check_test_results` | Generic pytest pass rate check. Doesn't decompose per-suite. |
| LLM exhaustion in `weekly_synthesis` / `trade_reviewer` / etc. | `_llm_call.py` writes `llm_failures.jsonl` + posts Discord (rate-limited 1h cooldown). **Sentinel reads neither today.** |

Net: Sentinel's *blast radius* covers Gamma transparently for **infra-level** events (cron staleness, broker port, journal schema, ghost positions, disk/memory). It is **blind to all Gamma-experiment-specific events** unless they happen to surface in `errors.db` via the catch-all logger.

---

## Step 1c — Gamma 4-arm failure modes NOT covered today

Mapped against the 9 surfaces in the brief plus 2 I noticed during the audit:

| # | Failure mode | Surfaced today? | Where it lives if it fires |
|---|---|---|---|
| **G1** | Per-arm state file drift > $1 | Partially — `reconcile_and_alert` posts when called from agent code, but **Sentinel has no awareness of frequency** (a recurring drift = repeated alerts is not aggregated). | `gamma_arm_<id>_account.json` |
| **G2** | Per-arm circuit breaker firing (one arm in 48h pause) | No — `circuit_breaker_active=true` lives in arm state file; no monitor reads it. **Operator-blind unless they happen to look at dashboard.** | `gamma_arm_<id>_account.json:circuit_breaker_active` + `circuit_breaker_until` |
| **G3** | `gamma_ranking_decisions.jsonl` growth anomaly or write failure | No — file is append-only; no size monitor, no per-scan presence check. If `_log_ranking_decision()` raises, only a `logging.warning` (line 694), no Discord. | `/root/quantai-v2/shared-data/logs/gamma_ranking_decisions.jsonl` |
| **G4** | Same-symbol multi-arm partial-broker-failure cancels | Discord post fires at cancel-time, but no rate tracking. **A run of repeated cancels (e.g., 5 in one week) wouldn't escalate.** | log line + Discord post (`gamma_agent.py:984`) |
| **G5** | Per-arm journal vs `trades.jsonl` union drift | No monitor. Both are appended independently from `gamma_agent.run_execute_4arm`. A trade present in per-arm but missing from union (or vice versa) is silent. | `gamma_arm_<id>_trades.jsonl` vs `trades.jsonl` |
| **G6** | Spread verifier blocking > 30% of universe | Discord post fires weekly when verifier runs (Monday 9:30 AM ET), but **no anomaly threshold**. Operator must visually compare with last week's count. | `gamma_spread_status.json` |
| **G7** | Gamma reflection-memory stub backlog growing | Partial — `reflection_reconciler.py` escalates after 3 days. **The 0–3 day window is invisible** (status=`llm_failed` or `pending_retry`). The new `collect_phase2.py` collector exposes counts on the dashboard, but Sentinel doesn't read it. | `memory/gamma_reflections.jsonl` |
| **G8** | Promotion-evaluator state changes (sample-size threshold crossed, near-tie crossed) | No — promotion evaluator is invoked manually via `--evaluate-promotion`. Nothing automatic detects "Arm B just crossed 80 trades and now passes the win-margin gate." | run-on-demand only |
| **G9** | Day-X mismatch (cron stopped firing on a market day) | Partial — `check_cron_freshness` catches generic staleness, but **experiment-day counter would still tick forward** based on `experiment_started_at` wall-clock, masking the gap. If 3 market days pass with 0 ranking_decisions appended, that's the real signal — invisible today. | `gamma_ranking_decisions.jsonl` line count per market day |
| **G10** *(audit-found)* | Trade ID prefix collision (`Ga###` reused across resets) | No — `gamma/arm_state.py` archives state on reset, but the in-file counter restarts from `001`. A reset followed by a fresh trade with same `Ga001` would create an ID collision with the archived journal. | per-arm state + journal |
| **G11** *(audit-found)* | `GAMMA_AB_TEST_ENABLED` accidentally toggled to `0` during a live experiment | Partial — Sentinel's `NEVER_TOUCH_PATHS` blocks `.env` writes, but a human reset would silently disable the test. No "experiment still active but flag=0" check. | `.env` line vs `agent-gamma-state.json:experiment_active` |

---

## Step 2 — Detection plan per uncovered failure mode

Each row is a self-contained spec the implementation commit can follow.

### Design notes that apply to all 11 checks

- **Implementation surface**: extend `system_monitor.py` with new `check_gamma_*` functions added to the `CHECKS` table. Sentinel's `gather_context` already reads `system-health-report.json`, so any check added there flows through to Sentinel's LLM prompt automatically — no `sentinel_agent.py` edit needed for visibility. Some checks (G7, G8) are better as standalone scripts because they need state across runs.
- **Channel convention**:
  - `info` — never alerts; surfaces only in dashboard tile
  - `warning` — Sentinel surfaces in next `observe` digest; no immediate Discord
  - `critical` — immediate post to `DISCORD_CHANNEL_SYSTEM_HEALTH` via Sentinel's `post(CH_HEALTH, ...)`; bypasses the digest queue
- **False-positive guard pattern**: store last-alert timestamp + threshold in `/var/dashboard/state/sentinel-gamma-alerts.json` (atomic write); each check reads + updates with cooldown logic (mirrors `_GHOST_ALERT_FILE` pattern at `position_monitor.py:870`).
- **Frequency**: every Sentinel cycle (so 7 weekday slots/day) unless noted otherwise.
- **Cost**: all checks are deterministic file reads. No LLM cost added.

---

### G1 — Per-arm state file drift

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_arm_drift()` in `system_monitor.py`. Reads all 4 `gamma_arm_<id>_account.json` files. For each, calls existing `reconcile()` from `gamma/arm_state.py` with the per-arm open trades from the matching journal. Returns the count of arms failing reconciliation and the worst Δ. |
| **Severity** | `warning` if 1 arm drifts; `critical` if 2+ arms drift OR a single arm Δ > $50 |
| **Alert** | `⚠️ Gamma arm reconciliation drift — arm(s) {ids} have equity invariant Δ {amount}. Existing per-arm alert already fired. Sentinel surfacing in case operator missed it.` → `CH_HEALTH` |
| **Frequency** | Every Sentinel cycle (7×/day weekday) |
| **FP guard** | Suppress if same `{ids, delta_bucket}` alerted in last 4h. State file: `sentinel-gamma-alerts.json` keys: `g1_<id>` → `{last_iso, last_delta_bucket}` |
| **Why this works** | The existing per-arm `reconcile_and_alert` fires *once* at the trigger moment; Sentinel re-detects on every cycle as long as the drift persists, which is exactly what the operator wants when triaging a recurring vs one-off drift. |

### G2 — Per-arm circuit breaker firing

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_circuit_breakers()`. For each arm state, check `circuit_breaker_active==true`. Returns list of arms with CB on + `circuit_breaker_until` timestamps. |
| **Severity** | `warning` if 1 arm CB; `critical` if 2+ arms CB simultaneously (suggests systemic problem, not noise) |
| **Alert** | `🛑 Gamma Arm {id} circuit breaker active until {ts} — {n} consecutive losses. Other arms continue trading.` → `CH_HEALTH` |
| **Frequency** | Every Sentinel cycle |
| **FP guard** | One-shot alert per `(arm_id, circuit_breaker_until)` tuple. Re-arm only when `circuit_breaker_active` flips back to false then true with a new `_until` timestamp. |
| **Why this works** | Pre-experiment Gamma had a single CB — universally observable. With 4 arms, an individual arm CB is invisible without this check. |

### G3 — `ranking_decisions.jsonl` write failure / growth anomaly

**Verified write volume (2026-05-11 audit):** `_log_ranking_decision()` is called exactly once per `run_scan_4arm()` invocation (one append per scan, regardless of how many arms/setups qualified — the record nests `ranks_per_arm` + `picked_per_arm` as dicts). The scan cron fires once per weekday at `30 20 * * 1-5`. **Baseline = 1 line per market day** (~5/week, ~22/month). Original 50/day threshold was wildly off.

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_ranking_decisions()`. Reads file mtime + line count. On weekdays after 20:30 UTC the day's mtime should be within 24h. Tracks daily increment in `sentinel-gamma-alerts.json` for both stagnation (0 entries on a market day after 21:00 UTC) and growth (≥2 entries in one day suggests scan re-ran). |
| **Severity** | Stagnation: `warning` off-market (weekend/holiday); `critical` on market day after 21:00 UTC. Growth: `warning` if ≥ 2 entries/day; `critical` if ≥ 5 entries/day (something looping). |
| **Alert** | Stagnation: `🔴 Gamma ranking_decisions.jsonl has 0 new entries on a market day — scan likely didn't fire or _log_ranking_decision failed silently.` Growth: `⚠️ Gamma ranking_decisions.jsonl unexpected: {n} new entries today (baseline 1/day). Scan may have re-run.` → `CH_HEALTH` |
| **Frequency** | Sentinel's 4 PM ET `observe` slot + 4:15 PM ET `apply` slot |
| **FP guard** | Skip on US market holidays (static holiday list in the check). Cooldown: 6h between alerts for the same direction. |
| **Why this works** | `_log_ranking_decision()` swallows exceptions to `logging.warning` — this is the only way to surface that silent failure. With 1/day baseline the growth threshold is sensitive enough to catch a single re-run, which itself is operationally interesting. |

### G4 — Partial-broker-failure cancel rate

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_partial_failures()`. Greps `gamma.log` (last 7 days) for `partial broker failure` lines, counts occurrences. Optionally — better — emit a structured `partial_failure_blocks.jsonl` from `gamma_agent.py:984` and read that (smaller scope: just count last 7d). |
| **Severity** | `info` if 0–2 in last 7 days; `warning` if 3–5; `critical` if ≥6 (suggests broker connectivity issue, not random IBKR errors) |
| **Alert** | `⚠️ Gamma partial-broker-failure cancels: {n} in last 7d (threshold 3). Affected symbols: {top 5}` → `CH_HEALTH` |
| **Frequency** | Daily, at 4 PM ET `observe` slot |
| **FP guard** | Cooldown 24h on the same severity bucket |
| **Why this works** | Individual cancels are normal noise (transient IBKR errors). A cluster is the operational signal. |

### G5 — Per-arm journal ↔ trades.jsonl union drift

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_journal_consistency()`. For each arm: load all trade IDs from `gamma_arm_<id>_trades.jsonl`, load all `arm_id={id}` trade IDs from `trades.jsonl`. Compute set difference both directions. Returns any non-empty differences. |
| **Severity** | `critical` if any difference > 0. There is no benign reason this can drift. |
| **Alert** | `🔴 Gamma journal consistency drift — Arm {id}: {n} trades in per-arm journal missing from trades.jsonl (or vice versa). IDs: {first 3}` → `CH_HEALTH` + the `propose_wait` card flow (this needs human review) |
| **Frequency** | Every Sentinel cycle |
| **FP guard** | One-shot per unique set of missing trade IDs. Re-alert only if the set changes. |
| **Trading-window bypass** | **Yes** — data-integrity check; fires immediately regardless of trading window. |
| **Why this works** | The dual-write pattern in `gamma_agent.py` (commit 3 §J) is meant to be backwards-compat with existing tools; any drift means the dual-write failed atomically and one file is missing data. |

### G6 — Spread verifier anomaly threshold

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_spread_verifier()`. Reads `gamma_spread_status.json`. Computes `(n_blocked + n_permanent_blocks) / total_symbols`. |
| **Severity** | `warning` if ratio ≥ 0.30; `critical` if ratio ≥ 0.50 |
| **Alert** | `🚧 Gamma spread verifier blocked {pct}% of universe ({n} of {total}) — exceeds 30% threshold. Last verified: {ts}` → `CH_HEALTH` |
| **Frequency** | **Once weekly** — first Sentinel cycle after Monday's 9:30 AM ET spread-verify run, i.e. Monday's 10:00 AM ET `observe` slot. `gamma_spread_status.json` doesn't change between weekly runs, so re-checking 7×/day adds no signal. |
| **FP guard** | Suppress if ratio bucket (in 10% buckets) hasn't changed since last alert |
| **Why this works** | Spread verifier weekly run already posts a Discord message with raw counts. Sentinel converts raw counts to anomaly detection. One check per week aligns with the data update cadence. |

### G7 — Gamma reflection stub backlog

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_reflection_backlog()`. Reads `memory/gamma_reflections.jsonl`. Counts entries with `reflection_status != "complete"` and `reflection_status != "manual_review"` (i.e., `llm_failed` + `pending_retry`). |
| **Severity** | `info` ≤ 5; `warning` 6–10; `critical` > 10. Threshold from brief. |
| **Alert** | `⚠️ Gamma reflection stub backlog: {n} entries pending retry (threshold 10). Next reconciler run: {next 22:00 UTC}` → `CH_HEALTH` |
| **Frequency** | Every Sentinel cycle |
| **FP guard** | Suppress if count hasn't changed since last alert |
| **Why this works** | The existing nightly reconciler only fires on escalation-to-`manual_review` after 3 days. The 0–3 day window is currently invisible. |

### G8 — Promotion-evaluator state change detection

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_promotion_state()`. Imports `gamma.promotion_evaluator.evaluate_promotion`, runs it with the current 4-arm state + journals + experiment day, compares the `decision` + `rule_applied` against the last-stored result in `sentinel-gamma-alerts.json:g8_last_decision`. On change, alert. |
| **Severity** | `info` when sample-size floor is crossed for an arm (e.g., Arm C just hit 80 trades); `warning` when rule transitions from `sample_floor` → `near_tie`/`win_margin`/`inconclusive_band`; `critical` when decision changes to `promote` or `hard_cap_default` |
| **Alert** | `🎯 Gamma promotion evaluator transition: {old_rule} → {new_rule}. Decision: {new_decision}. Winner: {winner or 'none'}. Day: {experiment_day}.` → `CH_HEALTH` + `CH_APPROVALS` (for `critical`) |
| **Frequency** | **Twice daily** — 8:30 AM ET `apply` (pre-market: catches overnight theta-driven equity transitions that flipped a sample-floor or near-tie band even with no exits) + 16:15 ET `apply` (post-close: catches in-day exit-driven transitions). |
| **FP guard** | Hash the decision dict (excluding timestamp); alert only on hash change |
| **Why this works** | The evaluator is operator-invoked today, but key transitions (Arm crosses 80 trades, near-tie window) are the exact moments the operator needs to plan the next evaluation. Twice-daily covers both overnight (theta moves on open positions can shift equity invariants and per-day Sharpe inputs) and in-day signal sources. |

### G9 — Day-X mismatch (cron stopped on market day)

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_experiment_day_consistency()`. Computes "expected ranking_decisions count" = approximate trading days elapsed since `experiment_started_at`. Compares against actual line count of `gamma_ranking_decisions.jsonl`. Allows ±2 day buffer for holidays. |
| **Severity** | `warning` if delta ≥ 2 days; `critical` if delta ≥ 3 days |
| **Alert** | `🔴 Gamma experiment day mismatch — wall-clock says day {X}, but only {Y} scan events recorded (expected ~{Z}). Possible: cron stopped, ibkr disconnect during scan, or _log_ranking_decision write failure.` → `CH_HEALTH` |
| **Frequency** | Daily 16:15 ET `apply` |
| **FP guard** | Use a static US-market-holiday list for the year to avoid weekend/holiday false positives. Cooldown 24h on same delta bucket. |
| **Why this works** | The dashboard's "Day X" advances purely on wall-clock. This check is the only way to detect a silent cron stoppage that would invalidate days of would-be trade data. |

### G10 — Trade ID prefix collision (audit-found)

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_trade_id_uniqueness()`. For each arm: load all `id` fields from per-arm journal **AND** the **3 most recent** archive files (`gamma_arm_<id>_trades_<reset_date>.jsonl.archive`, sorted by reset_date desc). Verify the set is unique. Capping at 3 keeps per-cycle read cost O(1) regardless of long-term reset count. |
| **Severity** | `critical` if any duplicate found |
| **Alert** | `🔴 Gamma Arm {id} — duplicate trade ID detected across journals: {trade_id}. Reset counter likely collided with archived trade.` → `CH_HEALTH` + `propose_wait` to `CH_APPROVALS` for human review |
| **Frequency** | Once at 8:30 AM ET `apply` (pre-market — gives operator the whole day to fix) |
| **FP guard** | One-shot per duplicate ID. Don't re-alert same ID. |
| **Trading-window bypass** | **Yes** — data-integrity check; fires immediately regardless of trading window (forces an `apply`→`observe` downgrade exception). |
| **Why this works** | This bug exists right now in the reset path (in-file counter restarts on reset). It would never fire until an operator actually resets the experiment, at which point this catches it before the new trade hits the broker. |

### G11 — Flag/experiment-active mismatch (audit-found)

| Field | Value |
|---|---|
| **Detection** | New `check_gamma_flag_consistency()`. Reads `agent-gamma-state.json:experiment_active` (collector output, reflects last seen `GAMMA_AB_TEST_ENABLED`). Also reads any arm with `total_trades > 0`. If `experiment_active=false` but any arm has trades, that's a stuck-flag mismatch. **Before firing**, reads `promotion_history.jsonl` (new file written by `gamma_agent.py --promote-arm --confirm` and `--reset-experiment --confirm`). If the most recent entry timestamp is < 24h ago, suppress (legitimate operator action accounts for the flag-off state). |
| **Severity** | `critical` |
| **Alert** | `🔴 Gamma experiment state inconsistency — collector reports experiment_active=false, but Arm(s) {ids} have {n} trades, and no recent --promote-arm/--reset-experiment entry in promotion_history.jsonl. Flag likely toggled manually without proper reset.` → `CH_HEALTH` + `propose_wait` |
| **Frequency** | Every Sentinel cycle |
| **FP guard** | (a) One-shot until consistency restored; (b) 24h suppression after most recent `promotion_history.jsonl` entry |
| **Trading-window bypass** | **Yes** — data-integrity check; fires immediately regardless of trading window. |
| **Why this works** | The flag flip in commit 5 is the only way the experiment activates. The new `promotion_history.jsonl` audit trail distinguishes legitimate operator state changes from accidental flag toggles. |

---

## Step 2 summary table

| # | Failure mode | Detection file | Severity | Frequency | FP guard |
|---|---|---|---|---|---|
| G1 | Per-arm equity drift | `gamma_arm_<id>_account.json` | warn/crit | 7×/day | 4h cooldown / delta bucket |
| G2 | Per-arm circuit breaker | `gamma_arm_<id>_account.json` | warn/crit | 7×/day | one-shot per CB-until |
| G3 | ranking_decisions write fail | `gamma_ranking_decisions.jsonl` | warn/crit | 2×/day | 6h cooldown |
| G4 | Partial-broker cancel rate | `gamma.log` or new jsonl | info/warn/crit | 1×/day | 24h cooldown / bucket |
| G5 | Journal union drift | per-arm + `trades.jsonl` | crit | 7×/day | one-shot per ID set |
| G6 | Spread verifier > 30% | `gamma_spread_status.json` | warn/crit | **1×/week** (Mon 10:00 ET) | bucket-based |
| G7 | Reflection stub backlog | `memory/gamma_reflections.jsonl` | info/warn/crit | 7×/day | count change |
| G8 | Promotion state change | computed | info/warn/crit | **2×/day** (8:30 + 16:15 ET) | decision hash |
| G9 | Experiment-day mismatch | computed | warn/crit | 1×/day | holiday list + 24h cooldown |
| G10 | Trade ID collision | per-arm + archives (cap 3) | crit | 1×/day | one-shot per ID |
| G11 | Flag/state mismatch | flag + arm state + promotion_history.jsonl | crit | 7×/day | one-shot until restored; suppress 24h after `--promote-arm`/`--reset-experiment` |

---

## Implementation plan (deferred — 4-commit split)

**Status:** awaiting explicit go-ahead. The Sentinel implementation can wait until mid-week (per operator note 2026-05-11) — the deep-scan task is more time-sensitive and runs first. 4-commit split below:

### Commit 1 — `system_monitor.py` checks + tests (no Sentinel touch)

- Add 11 new `check_gamma_*` functions to the `CHECKS` table:
  - State-free (read current files only): G1, G2, G5, G6, G7, G10, G11
  - State-keeping (read/write `sentinel-gamma-alerts.json`): G3, G4, G8, G9
- New file `tests/unit/test_sentinel_gamma_coverage.py` — 22 tests (11 happy-path + 11 trigger)
- Sentinel still ignores these on first deploy — they only surface in `system-health-report.json`. Sentinel reads them automatically on next cycle since `gather_context()` already includes the full report.
- **Diff estimate**: ~400 LOC checks + ~300 LOC tests = 700 LOC across 2 files
- **Test count**: 1337 → ~1359

### Commit 2 — `sentinel/gamma_alerts_state.py` + state-management tests

- New file `v2/shared-data/scripts/sentinel/gamma_alerts_state.py` — atomic JSON read/write helpers for cross-cycle state (cooldowns, one-shot tracking, decision hashes). Mirrors `_GHOST_ALERT_FILE` pattern from `position_monitor.py:870`.
- Tests for atomic-write, cooldown windows, bucket-based suppression, decision-hash change detection
- **Diff estimate**: ~80 LOC helper + ~150 LOC tests = 230 LOC across 2 files
- **Test count**: ~1359 → ~1369
- *Note*: Commit 1's state-keeping checks (G3, G4, G8, G9) will use a tiny inline shim until Commit 2 lands; or split differently — happy to reorder commits 1 and 2 if cleaner.

### Commit 3 — `error-catalog.json` + Sentinel `SYSTEM_PROMPT` update

- 11 new entries in `docs/error-catalog.json` for each `check_gamma_*` ID with runbook stub + auto-action hints (`info`/`warn` → digest, `critical` → immediate)
- One-paragraph addition to Sentinel `SYSTEM_PROMPT` at `sentinel_agent.py:636`: "Note: 11 gamma-experiment-specific checks now feed into the system_health bundle as `check_gamma_*`. Critical-severity gamma checks G5, G10, G11 should be treated as immediate-action regardless of trading window (these are data-integrity checks). Other gamma checks follow normal observe/apply flow."
- **Diff estimate**: ~50 LOC catalog + ~30 LOC prompt edit = 80 LOC across 2 files. No new tests (covered by Commit 1's happy-path runs against the catalog).

### Commit 4 — `NEVER_MODIFY_PATHS` extension + `promotion_history.jsonl` emit

- `sentinel_agent.py:NEVER_MODIFY_PATHS` extended with glob patterns:
  - `memory/*_reflections.jsonl` (covers Alpha/Beta/Gamma)
  - `journal/paper/gamma_arm_*_trades.jsonl` (covers all 4 arms with one entry)
- `gamma_agent.py:run_promote_arm()` writes one append-only entry to `/root/quantai-v2/shared-data/logs/promotion_history.jsonl` on `--confirm`:
  ```json
  {"ts": "<iso>", "action": "promote_arm", "winner": "b",
   "rule_applied": "win_margin", "experiment_day": 60, "decision": {...}}
  ```
- `gamma_agent.py:run_reset_experiment()` writes equivalent entry on `--confirm`:
  ```json
  {"ts": "<iso>", "action": "reset_experiment", "reason": "...",
   "experiment_day_at_reset": <int>}
  ```
- G11's `check_gamma_flag_consistency()` reads this file for 24h suppression
- Tests: append-only behavior, G11 24h suppression window, both action types
- **Diff estimate**: ~50 LOC catalog + ~80 LOC gamma_agent + ~50 LOC sentinel + ~100 LOC tests = 280 LOC across 4 files
- **Test count**: ~1369 → ~1377

### Total estimate

- **Code**: ~660 LOC across 5 files (system_monitor, alerts_state, sentinel, gamma_agent, error-catalog)
- **Tests**: ~600 LOC across 3 new test files (~40 new tests)
- **Test count**: 1337 → ~1377 (+40)
- **No new cron entries.** **No new Discord channels.** **No changes to existing thresholds.**

### Pre-deploy smoke checks (before Commit 1 lands)

- Run `system_monitor.py --once` locally; verify all 11 new checks return `ok` against current production state (experiment is at day 0; no failure surface has fired yet)
- Manually trigger one synthetic drift in a temp arm state and confirm the matching check fires `warning`/`critical` with the right message
- Confirm `sentinel-gamma-alerts.json` is created with mode 0644, root-owned, in `/var/dashboard/state/`

---

## Review decisions (resolved 2026-05-11)

The 5 open questions from the initial draft have been resolved by the operator:

1. **G8 frequency — RESOLVED**: twice daily (8:30 AM ET + 16:15 ET). Overnight position theta on open per-arm positions can shift equity invariants and per-day Sharpe inputs even without exits, which can flip an arm across a sample-size or near-tie band before the operator wakes up. Both checks fire post-market-event (after overnight settle, after in-day exits) so transitions are caught at the right phase.
2. **G10 archive cost — RESOLVED**: cap at the 3 most recent archive files (sorted by reset_date desc). Keeps per-cycle read cost O(1) regardless of long-term reset count.
3. **G11 false-positive — RESOLVED**: write a new `promotion_history.jsonl` audit log when `gamma_agent.py --promote-arm --confirm` or `--reset-experiment --confirm` runs. G11 reads this file and suppresses the alert if the most recent entry is < 24h ago (legitimate operator action accounts for the flag-off state).
4. **Trading-window bypass — RESOLVED**: G5 (journal union drift), G10 (trade ID collision), G11 (flag/state mismatch) bypass the trading-window `apply`→`observe` downgrade — these are data-integrity checks and need immediate action. Everything else queues to the next non-trading window via the normal `propose_wait` flow.
5. **NEVER_MODIFY_PATHS extension — RESOLVED**: yes, belt-and-braces. Add glob patterns `memory/*_reflections.jsonl` (covers Alpha/Beta/Gamma) and `journal/paper/gamma_arm_*_trades.jsonl` (covers all 4 arms with one entry). These were already covered indirectly by `NEVER_TOUCH_PATHS` journal directory but explicit-allowlist makes the intent legible in code review.

---

## What this plan does **not** do

- No new LLM prompt logic — Sentinel will see the new checks in the deterministic bundle and surface them through its existing observe/apply flow.
- No new Discord channels.
- No mutation of Gamma agent code (`gamma_agent.py`, `gamma/*`).
- No changes to existing thresholds (`reconcile_and_alert` $1 invariant, etc.) — only adds Sentinel-level frequency/aggregation on top.
- No new cron entries — `system_monitor.py` already runs on its existing schedule; we plug into its `CHECKS` table.

## Files to create (post-approval)

| Path | Purpose | Commit |
|---|---|---|
| `v2/shared-data/scripts/sentinel/gamma_alerts_state.py` | Atomic JSON read/write helpers for cooldowns / decision hashes / one-shot state | 2 |
| `v2/shared-data/scripts/sentinel/__init__.py` | Package init (empty) | 2 |
| `v2/shared-data/tests/unit/test_sentinel_gamma_coverage.py` | 22 tests (11 happy + 11 trigger) for the new checks | 1 |
| `v2/shared-data/tests/unit/test_sentinel_alerts_state.py` | Atomic-write, cooldown, hash-change tests for the state helper | 2 |
| `v2/shared-data/tests/unit/test_promotion_history.py` | Append-only behavior for `--promote-arm` / `--reset-experiment` audit log | 4 |
| `/root/quantai-v2/shared-data/logs/promotion_history.jsonl` | Runtime audit log written by gamma_agent.py (auto-created on first --confirm) | 4 |
| `/var/dashboard/state/sentinel-gamma-alerts.json` | Runtime cooldown/one-shot state (auto-created by Commit 1's state-keeping checks) | 1 |

## Files to modify (post-approval)

| Path | Modification | Commit |
|---|---|---|
| `v2/shared-data/scripts/system_monitor.py` | +11 `check_gamma_*` functions + 11 entries in `CHECKS` table | 1 |
| `v2/shared-data/scripts/sentinel_agent.py` | (a) One-paragraph addition to `SYSTEM_PROMPT`; (b) `NEVER_MODIFY_PATHS` extended with `memory/*_reflections.jsonl` + `journal/paper/gamma_arm_*_trades.jsonl` patterns | 3 + 4 |
| `v2/shared-data/scripts/gamma_agent.py` | `run_promote_arm()` + `run_reset_experiment()` append to `promotion_history.jsonl` on `--confirm` | 4 |
| `docs/error-catalog.json` | 11 new entries (auto-action hints, runbook links) | 3 |

---

**Status: plan only. No code changes made. Awaiting review.**
