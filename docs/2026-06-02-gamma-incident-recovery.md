# Gamma Restart Incident — Recovery Log (2026-06-01 / 2026-06-02)

**Status:** Partial recovery complete. APD flat + reconciled. WMT still open
(after-hours, didn't fill). Code fixes landed. Final WMT flatten + clean
reset deferred to next session before 13:30 UTC market open.

## What happened (incident chain)

1. **2026-06-01 — Gamma 4-arm reset (Option A "clean restart").** Pre-reset
   close of 6 stale broker positions succeeded; `gamma_agent --reset-experiment`
   zeroed all 4 arms to $10K. Clean.
2. **Un-pause → execute cron fired (13:33 UTC).** 8 fresh Gamma entries
   placed (WMT + APD per arm). FSM stamped them `ACKED`. Broker filled them
   13:46 UTC.
3. **FSM resurrection gap.** `_promote_pending_entries` polls
   `broker.get_order_status(oid)`, which returns `None` for already-FILLED
   orders. So the PENDING entries never auto-promoted to OPEN.
4. **Manual promotion script (operator-run) hit the dedupe bug.** The reset
   truncated per-arm journals but NOT the union journal. The agent's
   `next_arm_trade_id` counter restarted at `001`, so the 8 new entries
   reused ids `Ga001`-`Gd002` that already existed in the union journal.
   The promotion script's `update-by-id` matched BOTH copies → 8 duplicate
   ids in the union journal.
5. **position_monitor duplicate-close cascade.** With two OPEN entries per
   id, position_monitor fired duplicate close orders. The broker executed
   each independently, over-closing the WMT spreads into an inverted
   position (-4 / +4) and churning APD into -12 / +12.
6. **OOM cascade (background, compounding).** Sentinel's `run_pytest_if_stale`
   race (known issue, commit `4b01ba3`) spawned a 28-deep recursive pytest
   fork chain consuming ~3.4 GB. RAM + swap hit 100%, OOM-killing litellm
   and pressuring the IBKR gateway Java process. 26 OOM kills in the window.

## Recovery actions (2026-06-02 evening session)

| Stage | Action | Result |
|---|---|---|
| 1 | Paused `position_monitor` cron; killed the 28 orphan pytest processes | RAM 100%→49%, swap 100%→50%, 1.9 GB free |
| 2 | Verified IBKR gateway (`systemctl is-active`, port 4002, connect test) | Active, 0.2s connect, account <IBKR_PAPER_ACCOUNT> |
| 3 | Flattened APD via direct ib_insync combo (BUY 12× 280C / SELL 12× 290C) | **APD flat.** WMT combo + single-leg both failed to fill after-hours |
| 3D | Pivot: WMT left open (paper book won't stage it after-hours) | WMT still -4/+4 |
| 4D | Marked Ga102-Gd102 (APD) CLOSED, restored arm cash | See P&L below |
| 5 | Three code fixes + 13 tests; full suite 2331 passed | Green |

## Realized P&L

- **APD flatten total: -$4,760** (verified from broker fills). Even split
  across 4 identical arm spreads = **-$1,190 / arm**.
  - Caveat: this includes unwinding the cascade-created excess (position
    grew to 12 contracts from the original 4). It over-states each arm's
    *pure* per-trade loss but keeps arm books reconciled with the broker's
    realized P&L.
- **WMT: not yet realized** (still open at -4/+4, unrealized ≈ +$200 net).

Post-reconciliation arm state:

| Arm | Cash | Realized P&L | APD trade |
|---|---|---|---|
| A | $8,661 | -$1,190 | CLOSED |
| B | $8,654 | -$1,190 | CLOSED |
| C | $8,658 | -$1,190 | CLOSED |
| D | $8,652 | -$1,190 | CLOSED |

WMT entries Ga101-Gd101 remain `status=OPEN` (reservation still held).

## The three code fixes

### Fix 1 — `next_arm_trade_id` dedupe (`gamma/arm_state.py`)
The counter now scans **both** the per-arm journal AND the union journal on
disk, taking the max. A per-arm reset that truncates only the per-arm file
can no longer cause an id collision with surviving union-journal entries.
Chosen over "archive the union journal on reset" because it's defensive
regardless of what reset does — even a manual journal edit can't reintroduce
a collision. (Option b from the recovery plan.)

### Fix 2 — FSM resurrection guard (`position_monitor.py`)
New `_detect_resurrected_pending()`: before phantom-escalation, check every
PENDING entry against live broker positions. If a PENDING entry's leg is
live at the broker (fill arrived after the status poll), **alert + log +
skip** — do NOT auto-promote (too risky without a full FSM fix) and do NOT
phantom-escalate. Operator reconciles manually. This is the documented
"alert and log, don't auto-promote" interim posture from
`docs/2026-06-01-fsm-resurrection-gap.md`.

### Fix 3 — duplicate-id close protection (`position_monitor.py`)
New `_duplicate_ids()` + a guard at the top of `main()`: if the journal has
any duplicate ids, position_monitor **halts the entire close loop**, alerts
once, writes the dashboard, and returns. Firing closes on a duplicated OPEN
entry doubles the close quantity and inverts the broker position — exactly
the 2026-06-01 failure. Belt-and-suspenders behind Fix 1.

13 new tests in `tests/unit/test_incident_guards.py`. Full suite green
(2331 passed, 6 skipped) as a single process — no fork bomb.

## What the operator did manually
- Ran the Option-A reset decision and the manual promotion script (which
  triggered the latent dedupe bug — now fixed).
- Authorized the recovery flatten + journal/arm mutations.

## Outstanding (next session, before 13:30 UTC)
1. **Flatten WMT** at market open (single-leg market orders fill reliably
   during RTH). Record realized P&L.
2. **Reconcile WMT**: mark Ga101-Gd101 CLOSED with realized P&L, restore arm
   cash.
3. **Re-run `gamma_agent --reset-experiment --confirm`** — now collision-safe
   (Fix 1). Verify new ids advance past the union-journal max.
4. **Un-pause**: Gamma crons + position_monitor cron, remove `entry_pause.flag`.
5. **Dry-run scan** to confirm clean before live entries.

## Current frozen state (end of this session)
- `entry_pause.flag`: PRESENT (entries blocked)
- Gamma crons: COMMENTED (paused)
- `position_monitor` cron: COMMENTED (paused — Stage 1)
- `LIFECYCLE_FSM_MODE`: still `enforce` (no effect while crons paused)
- Broker: APD flat, WMT -4/+4 open, 0 working orders
- RAM: stable, ~1.9 GB free, pytest fork bomb killed
- IBKR gateway: healthy

## Root-cause backlog (not fixed tonight)
- **Sentinel pytest fork race** (`4b01ba3`): the OOM driver. Needs the
  4-part fix (timeout +5×, pre-write cooldown state, pytest lockfile,
  litellm `--memory=1g`). Highest-priority infra fix.
- **Proper FSM ACKED→FILLED reconciliation**: the resurrection guard only
  alerts. The real fix is reconciling ACKED entries against broker
  positions/fills instead of relying on `get_order_status` (which can't see
  filled orders).
