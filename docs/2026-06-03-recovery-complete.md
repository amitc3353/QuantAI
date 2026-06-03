# Gamma Incident — Recovery Complete (2026-06-01 → 2026-06-03)

**Status:** Recovery complete. Account flat, journal reconciled to broker
reality, experiment reset to a clean $10K baseline, Gamma + position_monitor
un-paused, all fixes shipped with tests. First live entry fires tomorrow on
the normal cron schedule.

## Timeline

| When | Event |
| --- | --- |
| 2026-06-01 | "Option A clean restart" of the Gamma 4-arm experiment. Pre-reset close of 6 stale positions succeeded; `--reset-experiment` zeroed arms to $10K. |
| 2026-06-01 13:33 UTC | Un-pause → execute placed 8 entries (WMT+APD per arm). Filled 13:46. |
| 2026-06-01 ~14:00 | **Incident:** reset truncated per-arm journals but not the union journal → `next_arm_trade_id` reused ids → 8 duplicate ids → position_monitor fired duplicate closes → broker positions inverted (APD −12/+12, WMT −4/+4). Compounded by the sentinel pytest fork-bomb OOM. |
| 2026-06-02 eve | Killed fork bomb, revived gateway, flattened APD (RTH). WMT combo left resting after-hours. Booked APD −$4,760 (flatten cost), 8 entries reconciled. Shipped 3 code fixes (f934073). |
| 2026-06-03 ~00:08 UTC | The resting WMT combo (`recovery-flatten-WMT-1780443669`) filled overnight. **Account fully flat.** |
| 2026-06-03 fork-bomb fix | a694c89 shipped the pre-push fork-bomb guard. |
| 2026-06-03 ~13:00 UTC | **Final recovery (this session):** re-based to IBKR realized, reset clean, un-paused. |

## IBKR-authoritative realized P&L

Once the account went fully flat, IBKR's own `RealizedPnL` = **−$3,763.38**
(total across all WMT+APD round-trips). This supersedes the 06-02 interim
booking of −$4,760 (which used APD flatten *cost*, not round-trip realized,
and ignored premium collected by the cascade's over-sells).

**Operator decision: even split across 4 arms = −$940.85/arm.** Booked so the
8 incident entries tie exactly to −$3,763.38:

| Arm | Entries | Realized | Equity (flat) |
| --- | --- | --- | --- |
| A | Ga101 (WMT) + Ga102 (APD) | −$940.85 | $9,059.15 |
| B | Gb101 + Gb102 | −$940.85 | $9,059.15 |
| C | Gc101 + Gc102 | −$940.84 | $9,059.16 |
| D | Gd101 + Gd102 | −$940.84 | $9,059.16 |
| **Total** | 8 entries | **−$3,763.38** | |

(Per-entry: WMT −$470.42 each; APD −$470.43/−$470.43/−$470.42/−$470.42. The
1-cent variance on arms C/D is rounding so the total ties to IBKR exactly
rather than to 4×−$940.85 = −$3,763.40.)

The 8 entries remain in the union journal as CLOSED for forensic record; the
reset archived the per-arm journals.

## Fixes shipped during recovery

| Commit | Fix |
| --- | --- |
| **f934073** | (1) `next_arm_trade_id` dedupe — counter scans the union journal so a per-arm reset can't reuse a surviving id (root cause of the duplicate-close cascade). (2) FSM resurrection guard in `_promote_pending_entries` — alert+skip a PENDING entry that already has a live broker position. (3) Duplicate-id close protection — position_monitor halts the close loop if any journal id collides. |
| **a694c89** | Pre-push pytest fork-bomb fix — `run_pytest_if_stale`/`run_graphify_if_stale` skip the subprocess when `PYTEST_CURRENT_TEST` is set. Killed the OOM driver that took out the gateway twice. Suite runtime dropped 374-593s → ~49s. |
| **(this session)** | Circuit-breaker-vs-reset fix — `consecutive_arm_losses`/`check_portfolio_gates_for_arm` accept `since_iso` (the arm's `experiment_started_at`); pre-reset closed losses no longer count toward the breaker. Without it, the 8 reconciled incident losses would have blocked all 4 arms for 48h on a fresh reset. 9 regression tests. |

## Step-2 error sweep findings

- **Real bugs, fixed now:** circuit-breaker-vs-reset (above) — it was restart-blocking.
- **Real bugs, deferred** (pre-existing, off the gamma trading path, in
  `docs/post-recovery-todo.md`): P1 sentinel reaction-poll 429 storm (no
  Retry-After backoff); P2 `qualifyContractsAsync never awaited` RuntimeWarning
  in the broker close-poll; P3 gamma_weekly_digest 403.
- **Expected during freeze (no action):** `pipeline` heartbeat stale (Alpha
  paused), `BETA_ENABLED=0` skips, `market_hours=False` pre-open.
- **Resolved by tonight's fixes:** fork-bomb (a694c89); incident-era
  lifecycle_shadow `EXIT_ACKED` divergences (all 8 entries now CLOSED/terminal).
  Zero new error-catalog entries since 06-01. Zero OOM kills since a694c89.

## Reset + restart verification

- Reset: 4 arms at $10K, fresh `experiment_started_at` (2026-06-03T09:01:45 ET),
  per-arm journals empty, 8 forensic CLOSED entries retained.
- **No id collision:** next ids are Ga103-Gd103 (the f934073 dedupe scanned
  the union journal and advanced past the forensic entries).
- Dry-run scan (`--scan --dry-run`): hit regime gate (normal, VIX 16.2), all 4
  arms unblocked, produced picks (SBUX, APD), exit 0.
- position_monitor (un-paused): ticking clean, "No open agent positions —
  idle", no spurious duplicate-id or resurrection guard firing.
- Broker: fully flat (0 positions, 0 open orders).
- Memory: ~1.7Gi available, swap residual/dormant (litellm swapped out during
  the incident, si/so=0 — not active pressure), 0 pytest orphans, gateway active.

## First live entry — tomorrow on schedule

Gamma's cron is `--scan` at 20:30 UTC (writes pending entries) and `--execute`
at 13:33 UTC (places them). So:
- Today 13:33 UTC execute: clean no-op (no pending files).
- Tonight 20:30 UTC scan: writes the first post-reset pending entries.
- **Tomorrow 13:33 UTC execute: first live entries → first real FSM
  entry-walk** (PROPOSED → SUBMIT_PENDING → ACKED → FILLED → OPEN).

The live FSM entry-walk could not be observed in this session because of that
schedule — not a failure, just the cadence. It is covered by unit tests
(test_trade_lifecycle, test_lifecycle_fake_ibkr) and the a694c89/f934073
guards. Watch tomorrow's 13:33 execute + the following position_monitor ticks;
verify the new entries (Ga103+) get a `state` field and walk to OPEN.

## Current state: TRADING-READY

- entry_pause.flag: REMOVED
- Gamma crons (scan/execute/verify-spreads) + position_monitor: ACTIVE
- Alpha + Beta: still paused (`ALPHA_ENABLED=0` / `BETA_ENABLED=0`, crons
  commented) — unchanged, per operator scope (Gamma-only restart)
- LIFECYCLE_FSM_MODE=enforce, GAMMA_ENABLED=1
- Arms: $9,059.15 / $9,059.15 / $9,059.16 / $9,059.16, all flat, experiment day 0
