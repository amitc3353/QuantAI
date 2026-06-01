# Post-Restart Hardening List

**Date:** 2026-05-18
**Status:** Items identified during regime gate + pre-restart verification.
           Do NOT action until the 4-arm experiment restart completes.

## Items

### (i) Reconciliation at Entry, Not Just Close

**Current state:** `reconcile_and_alert()` (arm_state.py line 462)
fires only on position close, called from position_monitor's
`_update_arm_state_on_close()`.

**Thin spot:** A crash between `append_arm_trade()` (gamma_agent.py
line 1158) and `save_arm_state()` (line 1162) would leave a journal
entry with no corresponding cash decrement in the arm state file.
The journal shows an open trade; the state file doesn't reflect the
cash reduction. The $1 invariant (cash + open_max_risk = current_equity)
would be violated, but nobody checks until the position closes.

**Probability:** Low. The two calls are <1ms apart in the same process.
Requires a kill -9 or OOM between those lines.

**Fix:** Add a `reconcile_and_alert()` call after `save_arm_state()`
in the `run_execute_4arm()` entry path. This way, any entry-time
drift is detected immediately and a Discord alert fires. Same pattern
as the close path.

**Risk:** Minimal. `reconcile_and_alert()` is read-only (it checks and
alerts but does not mutate state). Adding it to the entry path cannot
cause side effects.

### (ii) Intel Decoupling Option B

**Current state:** Option A shipped (standalone cron, 2026-05-18).
`run_pipeline.py` still calls `run_script("market_intelligence.py")`
at line 215. The call is a harmless no-op (90-min freshness skip) but
the coupling remains in code.

**Fix:** Remove the `run_script()` call from `run_entry()`. Have the
pipeline read the cached file like all other consumers. One producer
(standalone cron), N readers.

**Details:** See `docs/2026-05-18-intel-decoupling.md` "What Is Deferred
(Option B)" section.

### (iii) experiment_started_at Fix

**Current state:** Already implemented in commit `0367883`
(`run_reset_experiment()` sets `experiment_started_at` on all 4 arms
after the reset loop).

**Applies on:** Next `--reset-experiment --confirm` invocation. All 4
arm state files currently show `experiment_started_at: None` because
they were initialized before the fix.

**No additional code needed.** The fix is in place; it activates when
the restart sequence runs.

### (iv) Cron Test Coverage for Intel Refresh

**Current state:** `test_identity_validation.py` has `EXPECTED_CRON_PATTERNS`
that verify expected cron entries are present. The new standalone
`market_intelligence.py` cron is not yet in the assertion set.

**Fix:** Add a regex pattern for the standalone cron to
`EXPECTED_CRON_PATTERNS` so future regressions (accidental deletion)
are caught by the test suite.

## Priority Order

1. **(iv) Cron test coverage — FIRST when hardening reopens.**
   This directly prevents the failure mode that caused the intel
   decoupling detour: safety-critical crons being pruned without
   test coverage catching it. The system has hit this twice already
   (Alpha kill-switch starving the refresh; sentinel weekend cut in
   3972dd5). One regex line in the test file locks the cron in place.
   Elevated from "trivial" to "first" on 2026-05-18 operator review.
2. (iii) is free — happens automatically on restart
3. (i) is low-risk, high-signal — add reconcile call at entry
4. (ii) is a small refactor — proper window with test verification
