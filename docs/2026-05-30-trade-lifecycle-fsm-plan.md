# Trade Lifecycle Reliability — Foundational FSM Refactor

**Status:** Phase 0 complete, Phase 1 in progress (2026-05-30).
**Workspace plan file (non-repo):** `/home/trader/.claude/plans/now-next-part-is-foamy-volcano.md`

## Context

We keep seeing recurring `PHANTOM_NEVER_FILLED` entries and intermittent
trouble across the trade lifecycle (open → track → close). The lifecycle
today is implicit — split across `autonomous_execution.py`, `beta_agent.py`,
`gamma_agent.py`, `position_monitor.py`, `_broker_ibkr.py` — with status
transitions implied by string comparisons against broker statuses. Several
defensive layers have shipped (two-phase journal `de7d230`, 3h phantom
escalation `21edf18`, P&L clamp `ba2b22e`, partial-fill safeguard) but
phantoms keep recurring. The fix is foundational: extract a
`TradeLifecycle` finite state machine that owns the full state graph.

## Phase 0 findings (2026-05-30, journal scan via `diagnostics/classify_phantoms.py`)

**21 phantoms total across 53 journal records. Zero unclassified after
refining the O3 rule to absorb `fill_status in (None, "", "unknown")`.**

| Class | Count | Pct | What it means |
| --- | --- | --- | --- |
| O3_STUCK_INDETERMINATE | 14 | 67% | Order ACKed at broker, never reached terminal state |
| O5_CLOSE_FAILURE | 7 | 33% | Close order issued, never completed (`working_close_order_id` set) |
| O1_NEVER_SUBMITTED | 0 | — | Agent never crashed pre-broker |
| O2_REJECTED_AT_BROKER | 0 | — | Broker never cleanly rejected an order |
| O4_OPEN_THEN_VANISHED | 0 | — | No fill-then-disappear cases |

**Implication for FSM scope:** only **2 of 12 transitions** carry the
phantom risk in practice — `ACKED → FILLED` (polling/timeout) and
`EXIT_SUBMITTED → CLOSED` (close-order completion). Phase 2's design
should aggressively simplify around this evidence rather than build
out the full 12-state diagram with equal weight on every transition.

### Full zombie inventory (21 records, for `backfill_state.py` migration)

**O5_CLOSE_FAILURE (7) — `working_close_order_id` set, close stuck:**

| ID | Source | fill_status | Age | close_reason |
| --- | --- | --- | --- | --- |
| Ga002 | gamma_arm_a | Submitted | 96h | auto_phantom_escalation_3h |
| Gb002 | gamma_arm_b | Submitted | 96h | auto_phantom_escalation_3h |
| Gc003 | gamma_arm_c | Submitted | 96h | auto_phantom_escalation_3h |
| Gd003 | gamma_arm_d | Submitted | 96h | auto_phantom_escalation_3h |
| Gb003 | gamma_arm_b | PreSubmitted | 96h | order_never_filled_at_broker |
| Gc004 | gamma_arm_c | PreSubmitted | 96h | order_never_filled_at_broker |
| Gd004 | gamma_arm_d | PreSubmitted | 96h | order_never_filled_at_broker |

The first 4 are the DE phantoms ROADMAP INFRA-5 was tracking. Diagnostic
confirms they are **not** auto-resolved — they still sit in the journal
with `working_close_order_id` set 4 days after the 3h escalator should
have cleaned them. Root cause: escalator marks status but doesn't clear
the close-order field, so they remain visible to the close path. The
FSM treats EXIT_SUBMITTED as a terminal-path state with its own deadlines
and cleanup.

**O3_STUCK_INDETERMINATE (14) — order_id present, never reached terminal:**

| ID | Source | fill_status | close_reason |
| --- | --- | --- | --- |
| M001 | manual | PendingSubmit | manual |
| M002 | manual | PendingSubmit | manual |
| A021 | agent_alpha | None | phantom_never_filled |
| A022 | agent_alpha | None | phantom_never_filled |
| A025 | agent_alpha | None | (none) |
| Ga003 | gamma_arm_a | PreSubmitted | order_never_filled_at_broker |
| Ga004, Gb004, Gc005, Gd005 | gamma arms a-d | unknown | pending_timeout_3h |
| Ga005, Gb005, Gc006, Gd006 | gamma arms a-d | unknown | pending_timeout_3h |

Manual M001/M002 are ancient (Jun 4) — long-overdue cleanup. A021/A022/A025
have `fill_status=None` (broker dict never persisted the status string to
journal). Gamma arm phantoms all carry `pending_timeout_3h` or
`order_never_filled_at_broker` close_reasons — they were observed and
escalated but never cancelled at the broker, so the underlying order may
or may not still be live at IBKR. Phase 4 (close-path FSM) must include
a broker.cancel_order call on escalation, not just a journal mark.

## What's already in place (do not duplicate)

| Capability | Location |
| --- | --- |
| Two-phase journal (`PENDING` → `OPEN` on fill confirmation) | `position_monitor.py:1613` `_promote_pending_entries()` |
| Phantom escalation after 3h | `position_monitor.py:1048` `_escalate_stale_phantoms()` |
| Ghost / journal-lie / entry-phantom detection | `position_monitor.py:1219` `reconcile_ghost_positions()` |
| Partial-fill recovery | `_broker_ibkr.py:637–801` + `_find_open_order_by_ref()` |
| Atomic journal rewrites | `position_monitor.py:162` `rewrite_journal_atomic()` |
| P&L clamp to structural max-loss | `derive_max_loss()` in `position_monitor.py` |
| Close-order poll loop (no resubmit) | `position_monitor.py:404` `place_close_order()` |
| Existing tests (mock broker) | `tests/unit/test_{phantom_escalation,pending_journal,phase5_partial_fill,pnl_clamp,expiry_sweep}.py` |

The FSM subsumes the first four. Atomic writes, P&L clamp, and expiry
sweep stay as primitives the FSM calls.

## Approach — `TradeLifecycle` FSM

### States (durable in journal `state` field unless noted)

```
PROPOSED              # in-memory only; agent minted coid, no broker call yet
SUBMIT_PENDING        # in-memory; placeOrder dispatched; awaiting ACK
ACKED                 # broker returned non-failure status, awaiting fill
FILLED                # at least one leg filled, awaiting positions() reconcile
OPEN                  # legs reconciled vs broker; subject to exit rules
EXIT_PROPOSED         # in-memory; exit rule fired this tick
EXIT_SUBMITTED        # close placeOrder dispatched
EXIT_ACKED            # close indeterminate; polling
CLOSED                # terminal — close filled AND legs absent from broker
REJECTED              # terminal — entry hit terminal-failure status
PHANTOM_NEVER_FILLED  # terminal — submitted/acked past deadline w/o fill
PHANTOM_VANISHED      # terminal — was OPEN, broker lost the position
EXPIRED               # terminal — leg expiry reached without close
```

Splitting today's monolithic `PHANTOM_NEVER_FILLED` into `REJECTED`,
`PHANTOM_NEVER_FILLED`, and `PHANTOM_VANISHED` gives actionable dashboard
categories. `PROPOSED`, `SUBMIT_PENDING`, `EXIT_PROPOSED` are ephemeral.

### Per-state deadlines (replace today's single 3h timer)

```
SUBMIT_PENDING : 60 s
ACKED          : 15 min    # primary phantom guard (Phase 0 says 67% of cases)
FILLED         : 5 min
EXIT_SUBMITTED : 60 s
EXIT_ACKED     : 30 min    # secondary phantom guard (Phase 0 says 33% of cases)
```

`OPEN` has no deadline. Today's 3h escalator becomes a safety net below.
**On deadline hit, FSM must call `broker.cancel_order` — not just mark the
journal.** This is the missing piece in the current `_escalate_stale_phantoms`.

### Idempotency

`client_order_id` (already minted by callers) is the idempotency key.
Every state-changing call (`submit`, `advance`, `close`) is a no-op if
the journal record is already at or past the target state for that coid.
Replay-safe by construction.

### Persistence model

Persist `state`, `last_transition_at`, `transitions_count` on the journal
record. Full transition history goes to an append-only sidecar
`state_transitions.jsonl` — never read on the hot path, only by the
replay test and dashboards.

## Module structure

New package `v2/shared-data/scripts/lifecycle/`:

- `states.py` — state enum, transition table, deadlines dict
- `trade_lifecycle.py` — `TradeLifecycle` class (`create / submit / advance / exit`)
- `journal_io.py` — state-aware reader/writer; reuses existing `rewrite_journal_atomic()`
- `broker_adapter.py` — typed wrapper around `_broker_ibkr.py.place_mleg_order` returning `SubmitResult.{ACCEPTED, INDETERMINATE, REJECTED, UNKNOWN}` so the FSM doesn't sprinkle string comparisons against `"submitted"` / `"presubmit"`
- `transition_log.py` — append-only sidecar journal writer
- `backfill_state.py` — one-shot status→state migration (uses zombie inventory above)

`_broker_ibkr.py` is **not edited** — it's the primitive the adapter wraps.

## Phased execution

### Phase 0 — Diagnostic ✅ DONE 2026-05-30

`diagnostics/classify_phantoms.py` (read-only) + 14 unit tests passing.
Findings above. Decision: only 2 transitions need tight guards
(ACKED→FILLED and EXIT_SUBMITTED→CLOSED).

### Phase 1 — Pause entry crons (in progress 2026-05-30)

Phase 0 evidence shifted scope: **Gamma is the actual phantom source**
(17 of 21 phantoms come from gamma arms). Alpha + Beta are already
agent-flag-paused (`ALPHA_ENABLED=0`, `BETA_ENABLED=0`); their cron
pause is belt-and-suspenders. Gamma cron pause is the real protective
action.

Two-layer pause:

1. **Soft flag** — touch `/root/quantai-v2/shared-data/cache/entry_pause.flag`. `autonomous_execution.py`, `beta_agent.py`, and `gamma_agent.py` entry paths return early when the flag is present.
2. **Hard pause** — `crontab -e` on the VPS, comment out:
   - `run_pipeline.py */15` (Alpha)
   - `beta_agent.py */15` (Beta)
   - `gamma_agent.py --scan` and `--execute` (Gamma)
   - **Leave `position_monitor.py */2` untouched** (must stay live for exits)
   - Leave reflection_reconciler, weekly_synthesis, gamma_weekly_digest, market_intelligence, sentinel all untouched (not entry paths)

### Phase 2 — Build FSM behind a flag (next session, COLD)

Phase 0's evidence allows aggressive simplification. The fresh design
pass should consider whether 12 states are needed or whether a leaner
graph (5-6 states focused on the two known-risk transitions) suffices.
**Do not roll straight from Phase 1 momentum.**

### Phase 3 — Shadow mode (1 week)
### Phase 4 — Exit-only enforce (1 week)
### Phase 5 — Full enforce + un-pause crons

(See workspace plan file for Phase 3-5 detail.)

## Test plan

| File | Coverage |
| --- | --- |
| `tests/conftest.py` (modify) | Shared `fake_broker` fixture (42 / 61 tests roll their own MagicMock today) |
| `unit/test_trade_lifecycle.py` | Per-transition tests over the table |
| `unit/test_lifecycle_property.py` | Hypothesis FSM property test |
| `unit/test_lifecycle_fake_ibkr.py` | 6 scenarios: stuck-Submitted, mid-poll fill, post-submit raise, partial fill, dup coid idempotent, close Cancelled→retry→Filled |
| `unit/test_lifecycle_replay.py` | Feed historical `trades.jsonl` through FSM; every CLOSED reaches CLOSED, every PHANTOM_* reaches one of the three new phantom terminals |
| `integration/test_lifecycle_paper_smoke.py` | Gated by `RUN_PAPER_SMOKE=1`. Submits deep-OTM spread that won't fill, waits 30s, cancels via FSM |

## Observability

Per-transition JSONL line to `state_transitions.jsonl`. Dashboard widgets:
trades-by-state pie, transitions-per-hour, oldest-non-terminal-per-state
table (row red past deadline). Discord alert on any deadline breach
(60-min cooldown by trade_id) — tighter than today's 3h ceiling.

## Verification

1. Phase 0 diagnostic output reviewed manually for classification accuracy ✅
2. New unit suite green (14 tests on `classify_phantoms.py` ✅; FSM tests in Phase 2)
3. Pre-existing tests stay green during shadow phase
4. `test_lifecycle_replay.py` shows no terminal-state mismatch vs. today's labels
5. 1 week of `lifecycle_shadow.log` shows zero divergences before exit-only enforce
6. `RUN_PAPER_SMOKE=1 pytest integration/test_lifecycle_paper_smoke.py` against IBKR paper
7. After each phase cutover: `pre_trade_check.py` 19/19 GO + dashboard "trades by state" matches journal

## Roadmap alignment

ROADMAP INFRA-5 (DE-phantom verification) is **superseded**: Phase 0's
diagnostic answered it (the 4 DE phantoms did NOT auto-resolve; they
need Phase 4 to clean up). INFRA-5 marked closed-superseded, this plan
becomes the active item.
