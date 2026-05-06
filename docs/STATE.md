# QuantAI — Current state snapshot

**Last updated**: 2026-05-06
**Read this with**: [architecture.md](./architecture.md) and [ARCHITECTURE_SUMMARY.md](./ARCHITECTURE_SUMMARY.md).

This file is **deliberately dated** — it captures what was live at the moment it was written. Replace it (don't append to it) on each major revision. Long-lived facts about how the system works belong in `architecture.md`; transient operational facts about *what's happening right now* belong here.

---

## Trading state

**HALTED** since 2026-05-05 ~16:45 ET. The cron lines for `run_pipeline.py`, `pre_trade_check.py`, `eod_summary.py`, `beta_agent.py`, `gamma_agent.py --scan`, `gamma_agent.py --execute`, `position_monitor.py`, and `event_moves_seeder.py` all have the `# HALTED 2026-05-05 INTC-mismatch-investigation:` prefix.

## Open positions

| ID | Symbol | Strategy | Status | Notes |
|---|---|---|---|---|
| A018 | INTC | Iron condor | Broker has 4 legs; journal CLOSED (incorrectly) | Holding to May 15 expiry |
| A020 | INTC | Iron condor | Broker has 4 legs at qty 4 in REVERSE direction; journal OPEN with mismatched qty | Holding to May 15 expiry |
| A021 | XOP | Iron condor | Broker empty; journal status corrected to `PHANTOM_NEVER_FILLED` 2026-05-06 | Resolved |
| A022 | INTC | Iron condor | Broker empty; journal status corrected to `PHANTOM_NEVER_FILLED` 2026-05-06 | Resolved |

A018 + A020 will reconcile automatically at May 15 option expiration. The journal can be manually corrected after the broker positions resolve.

## Why the halt

Multiple bugs in the trading path created journal-vs-broker mismatches:

- **Bug A** (A018) — close-path treated `Cancelled` status as success. Fixed 2026-05-04.
- **Bug B** (A020) — close-path retried on `Submitted` instead of polling. Fixed 2026-05-05.
- **Bug C** (A021/A022) — entry-path treated `Cancelled` as success. Fixed 2026-05-05 (added `_BROKER_TERMINAL_FAILURE_STATUSES` check).

The fixes are in place. The leftover broker state from before the fixes is what's holding the halt.

## Infrastructure state

- IB Gateway: `active`
- ClawRoute: `active`
- LiteLLM: still running (Docker, legacy)
- OpenClaw: `active`
- Sentinel: running on schedule
- Dashboard: live at `https://quantai.tail1465ff.ts.net/`
- Heartbeat monitor: running every 2 min, alerts pipeline-silent-stale (expected, halt-related)

## Trade counts to date

Live since 2026-04-27 (IBKR migration date):

- Alpha (A###): ~22 entries (most fully closed; A018 + A020 in unwind state)
- Beta (B###): a small number live; weekly rate increasing as event-moves seeder fills
- Gamma (G###): **0 trades**. The Connors RSI(10) < 30 + above-200-SMA filter has produced zero qualifying setups in 7 trading days × 27 instruments. By design.

## Recovery plan

Pre-defined sequence (originally in a plan file under `~/.claude/plans/`):

1. Wait for May 15 expiration → broker positions auto-flatten
2. Manual journal corrections for A018 / A020 (mark CLOSED with realized P&L)
3. Run `reconcile_audit.py` until exit code 0
4. Re-enable `position_monitor` cron only; observe 10 min
5. Re-enable Beta + Gamma `--scan`; let Beta complete one clean trade
6. Re-enable Gamma `--execute` and full Alpha pipeline

## Known good as of this writing

- Phase 5b partial-fill safeguard
- All four agents' identity files
- Dashboard renders all 4 agents (post 2026-05-06 audit fix)
- Architecture diagram includes Sentinel + ClawRoute (post audit fix)
- Bull/Bear PY templates in debate chamber

---

This snapshot will be wrong by next week. That's the design.
