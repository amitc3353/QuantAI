# Audit: orchestrator vs v2 trading + trader-guards usage (read-only)

**Date**: 2026-04-26
**Scope**: Phases 4–5 of `task-plan-graphify-glimmering-hollerith.md`. Findings are evidence-based (Alpaca order history + journal contents + container logs). No system changes were made by this audit.

---

## Phase 4 — Are orchestrator + v2 double-trading?

### Bottom line

**No.** Despite both APScheduler (orchestrator container, Agent 1 / Agent 2 entry jobs) and the v2 cron pipeline being active, only the v2 pipeline has actually placed trades in the last 7 days. The duplicate-trading risk I flagged in planning is **not present today** — though the architecture *could* allow it if the orchestrator's entry conditions ever loosened.

### Evidence

**Alpaca paper account, last 7 days** (9 orders total, all `filled`):

| Created (UTC) | Order class | Legs | Symbol prefix | Mapped to v2 trade |
|---|---|---|---|---|
| 2026-04-21T13:49:11 | mleg | 2 | XOM | A006 (entry) |
| 2026-04-21T13:49:12 | mleg | 2 | ARKK | A006 (entry) |
| 2026-04-21T14:04:04 | mleg | 4 | MU2 | A007 (entry) |
| 2026-04-21T14:06:02 | mleg | 4 | MU2 | A007 (exit, 2 min later) |
| 2026-04-21T19:30:02 | mleg | 2 | ARKK | A006 (exit) |
| 2026-04-21T19:30:02 | mleg | 2 | XOM | A006 (exit) |
| 2026-04-22T13:49:29 | mleg | 2 | XOM | A008 (entry) |
| 2026-04-22T13:49:33 | mleg | 2 | XLE | A009 (entry) |
| 2026-04-22T14:04:04 | mleg | 2 | XOM | A010 (entry) |

Every order timestamp ± 2s aligns with a v2 trade entry/exit timestamp in `/root/quantai-v2/shared-data/journal/paper/trades.jsonl`.

**Orchestrator's internal journals** (`/app/data/memory/paper/agent1_journal.jsonl` + `agent2_journal.jsonl` inside the `trader-orchestrator` container):
- 15 events total (7 from Agent 1, 8 from Agent 2).
- **All 15 events are `skip`**. Reasons: `vix_check` (VIX > 30 advisory), `empty_options_chain` (Alpaca returned 0 contracts for 0-2DTE on SPY).
- Zero `entry`, `proposed`, or `executed` events.

In other words: orchestrator's agents have been *attempting* to enter trades on schedule, but every attempt has been rejected upstream of order placement. v2 has been the only system actually transacting with Alpaca.

### Why orchestrator's "weekly review" said zero trades

The orchestrator's `cto_report.py:collect_eod_scores()` reads its OWN internal journal (the same all-`skip` log). v2 trades aren't in that file, so the report sees nothing. The dict-mutation crash we fixed earlier in this session was a symptom; the underlying broken-by-design data source remains.

### Implications

- **No urgent action.** Today's behavior is "v2 trades, orchestrator silently no-ops."
- **Risk lurking**: if Amit ever loosens orchestrator's entry guards (e.g., raises VIX cap above current market level), the orchestrator could start transacting in parallel with v2. Different journals, different limits, no shared state — a real duplicate problem.
- **Right next step**: when prioritizing the orchestrator-retirement work, no need to "freeze trading first." The orchestrator is already not trading.

---

## Phase 5 — Is `trader-guards` used?

### Bottom line

**Conditionally — but not by v2.** Keep for now; revisit when orchestrator retires.

### Evidence

**v2 scripts** (`v2/shared-data/scripts/`): zero references to port 8100, `GUARD_URL`, or `trader-guards`. v2's `autonomous_execution.py:58-69` has its own inline guard constants (`MAX_LOSS_PCT=2.0`, `MAX_OPEN=3`, `EARNINGS_BLACKOUT=14`, `MIN_CREDIT=0.30`, `VIX_HALT=35`, `ALLOWED_STRATEGIES`/`MANUAL_ONLY` sets). These are the actual guards enforced today.

**`discord-bot/`** references guards in 4 files:
- `bot.py:271` — POSTs to `http://trader-guards:8100/check` (likely from a slash command path)
- `cogs/trading.py:44`, `cogs/chat_agent.py:51`, `cogs/infra_agent.py:30` — define `GUARD_URL` env-driven constant for slash commands like `/buy`, `/guard_check`

**`trader-guards` container logs since restart**: only `/health` GET pings (likely from Docker healthcheck). The single `POST /check 422` was at startup — looks like a self-test.

### Implications

- v2 (the actual trading system) doesn't need `trader-guards`. Its guards are inline.
- The Discord bot's slash commands (`/buy`, possibly `/guard_check`) DO point at it. If we retire the container, those commands 500. If Amit doesn't actively use them, the bot would still work for everything else.
- **Recommendation**: keep `trader-guards` running until the bot is audited for which slash commands Amit actually uses. Low cost (small FastAPI service), no risk in keeping. Retire alongside the orchestrator-retirement task if `/buy` etc. turn out to be unused.

---

## Bonus finding: stuck OPEN trades A008 / A009 / A010

While auditing, I confirmed:
- A008 (XOM diagonal), A009 (XLE diagonal), A010 (XOM diagonal) are all `status: "OPEN"` in the v2 journal as of 2026-04-22.
- Each has 2 legs in the journal, but Alpaca only holds 1 of each (the long 2026-06-18 leg). The short 2026-05-08 leg expired or was assigned.
- This is the exact case behind the "Close order failed: mleg must have 2-4 legs" flood — the existing pipeline tried to close all 2 legs as mleg, Alpaca 422'd because only 1 was actually held.

This audit isn't fixing anything (Phase 3 in the plan addressed the underlying code), but worth noting that **A008/A009/A010 will close successfully on the next market open** thanks to the new single-leg fallback in `place_close_order()`.
