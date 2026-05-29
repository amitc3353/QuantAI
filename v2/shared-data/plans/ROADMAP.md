# QuantAI Roadmap

## Status

**Last updated:** 2026-05-29

Shipped 2026-05-11:
- **Gamma universe expansion** (PR #4) — 27 → 155 symbols, scanner F0 filter + parallelism. Live since 2026-05-09.
- **Gamma 4-arm A/B/C/D ranker experiment** — 5 commits (`85f9fcb` rankers, `4e6d0da` arm state, `51c3f26` orchestration, `1b9def3` reporting, `d8dbd8c` flag flip + sentinel freeze) + test fix `fd37014`. Flag flipped, Day 0 cron-fires Monday 16:30 ET.

Shipped 2026-05-09: Phase 2 Item #1 (reflection memory + multi-symbol retrieval + reconciler cron) and Item #2 (JSON parse hardening + retry envelope). All Phase 1 operational gates shipped earlier.

## Live experiments

- **Gamma 4-arm A/B/C/D ranker test**
  - Day 0: **2026-05-11**
  - First promotion eval: **2026-07-10** (day 60)
  - Hard cap: **2026-11-07** (day 180; if still inconclusive, ship Arm A by default)
  - Capital: $10K/arm × 4 arms = **$40K** virtual, single IBKR paper account DUP851506
  - Arms: A=RSI_ONLY (control), B=COMPOSITE, C=WEIGHTED_BLEND, D=REWARD_RISK_FIRST
  - Pre-committed promotion rules in `gamma/promotion_evaluator.py` (sample_floor → win_margin+Sharpe → near_tie (Ockham A>D>B>C) → inconclusive_band → hard_cap)
  - Frozen during test: universe, sectors, caps, ranker logic, thresholds (sentinel `NEVER_MODIFY_PATHS`)
  - Friday weekly digest: `30 20 * * 5` cron → Discord
  - Operator commands: `gamma_agent.py --evaluate-promotion | --promote-arm <a|b|c|d> --confirm | --reset-experiment --reason "..." --confirm`
  - Emergency stop: `GAMMA_AB_TEST_ENABLED=0` in `.env`

## Critical Infrastructure Backlog (added 2026-05-29)

These items are queued separately from the phase plan because they are
operational/infrastructure fixes rather than product features. Full design
notes for each item are in `docs/4arm-experiment-pnl-exit-fix-plan.md`
under "Operational Flags". The IDs here are stable references for cross-doc
linking.

- [ ] **INFRA-1 — OOM cascade / pytest fork loop fix** 🔴 CRITICAL
  - **Symptom:** 60+ kernel OOM kills on 2026-05-29 (mostly litellm container),
    triggered by ~28 concurrent pytest processes consuming ~3.4 GB on a 3.7 GB VPS.
  - **Root cause:** `sentinel_agent.run_pytest_if_stale()` has three compounding
    bugs — PYTEST_TIMEOUT (300s) < real suite runtime (~480s), `last_pytest_run`
    state written only after success (timeouts leave cooldown stale), no
    file-lock specific to pytest. litellm container has no Docker memory limit.
  - **4-part fix** (single focused session):
    1. Sentinel: bump `PYTEST_TIMEOUT` to 900s, write `last_pytest_run` BEFORE
       `subprocess.run`, add `fcntl.LOCK_EX | LOCK_NB` on `/tmp/sentinel_pytest.lock`
    2. Docker: add `--memory=1g --memory-swap=1g` to litellm container
    3. Audit test-internal pytest spawners (suspect:
       `test_sentinel_auto_actions.py:138`)
    4. Operational triage now: `pkill -f 'python3 -m pytest'` + `docker restart litellm`
  - **Files:** `v2/shared-data/scripts/sentinel_agent.py` (lines 1427-1509),
    Docker compose / run script for litellm container.

- [ ] **INFRA-2 — Sentinel hallucination dedup + 429 backoff** 🟡 MEDIUM
  - **Symptom:** Sentinel log floods with "hallucinated — skipped <name>" entries
    (correctly caught by safety rails) and "WARN: reaction-poll failed: HTTP 429"
    on each apply cycle. System functions correctly; this is log hygiene.
  - **Fix:** Add a fingerprint suppression cache that catches fuzzy matches
    (not just exact strings) so repeatedly-hallucinated systemd unit names get
    suppressed at proposal time. Add exponential backoff on reaction-poll loop.
  - **Files:** `v2/shared-data/scripts/sentinel_agent.py` (lines 349, 1180+, 1315).

- [ ] **INFRA-3 — KARNA-side false backup alert silence** 🟢 LOW
  - **Symptom:** KARNA posts "Daily Backup FAILED" to Discord every morning at
    02:00 UTC even though the system cron backup succeeds.
  - **Status:** QuantAI-side verifier already shipped (`check_karna_backup_freshness`
    in `system_monitor.py`, commit `4fa551f`). Remaining work is OpenClaw-side
    — change KARNA's routine from "exec the backup" to "verify the log".
  - **Files:** Outside this repo (OpenClaw config / KARNA scripts).

- [ ] **INFRA-4 — Gemini embedding API key rotation** 🟢 LOW
  - **Symptom:** "Memory search unavailable — Gemini embedding API key invalid"
    in KARNA's 02:00 UTC routine.
  - **Fix:** Rotate Gemini key in OpenClaw config, or switch to OpenAI
    text-embedding-3-small / local model via litellm.
  - **Files:** Outside this repo.

- [ ] **INFRA-5 — Verify Commit 4 auto-resolved DE phantoms** 🟡 MEDIUM
  - **Context:** Commit 4 (`21edf18`, phantom escalation) shipped 2026-05-29.
    The 4 live DE phantoms (Ga002/Gb002/Gc003/Gd003) should have auto-resolved
    within ~3h of the next position_monitor market-hours cycle, restoring
    $2,066 of arm cash. If the OOM cascade starved the cron cycles, this may
    still be pending.
  - **Action:** Read `journal/paper/trades.jsonl` for the 4 trade IDs; if any
    still show `status: OPEN`, investigate why escalation didn't fire and either
    manually mark PHANTOM_NEVER_FILLED or fix the underlying cause.

## Current Phase

**Phase 2 — Memory & Robustness** (weeks 4-5 per original plan)

Remaining items (recommended order):
- [ ] **#16 — Skills loader first** (`_skills_loader.py` + `skills/` directory with 8 Gamma skill files + Alpha/Beta equivalents)
- [ ] **#11 — Operator feedback memory** (Friday digest → Discord replies → `operator_feedback.jsonl` → judge injection)

Rationale for #16 → #11 order: the skills loader is foundational infrastructure that #11's digest-reply handler can lean on for prompt assembly. Doing #16 first lets #11 reuse the loader rather than duplicating prompt-injection plumbing.

Next session focus: Phase 2 closeout (#16 then #11) while Gamma experiment runs autonomously in the background. Watch dashboard https://quantai.tail1465ff.ts.net/ for the experiment banner + weekly digest.

## Phase Queue

**Phase 3 — EV Scoring + Adaptive Learning** (weeks 6-7)
- #3 — OptionLab EV/POP gate (pre-trade expected value + probability of profit)
- #4 + #6 merged — Composite trust score per (strategy, regime) replacing Thompson + circuit breaker

**Phase 4 — Portfolio Intelligence + Calibration** (weeks 8-10)
- #5 — Cross-agent portfolio Greeks aggregation
- #7 — Vector store for similar-setup retrieval (chromadb)
- #10 — Regime detector calibration from outcomes
- #14 — Strategy graceful re-classify on exhaustion
- #17 — Gamma overnight-gap drop logging

**Phase 5 — Backtest Infrastructure** (weeks 11-12)
- #8 — Backtest harness (Optopsy + Polygon options data)
- Gamma Connors revalidation on 155-symbol universe with 2026 data (post-experiment)

## Backlog

#13, #15, #18, #19, #20, #21, #27, #28

## How to update

After each shipped item, update Status + remove from Current Phase. After a phase completes, promote next phase to Current. Update Live experiments section when promotion/reset/hard-cap fires.
