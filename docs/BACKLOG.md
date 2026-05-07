# QuantAI Backlog

Captured ghost-TODOs and other queued work. Entries marked **trading-safety** require
review before any other work in their area.


## Test infrastructure: 6 files have collection errors

**Category:** test infrastructure  
**Effort:** 30 min – 1 hr  
**Flagged:** 2026-05-07 (pre-push hook audit)

Six test files use module-level `from conftest import <helper>` which fails
when pytest collects from sub-directories. The conftest schema-assertion
helpers are designed to be pytest fixtures, not importable as a plain module.

The pre-push hook works around this with `--continue-on-collection-errors`.
The underlying import-at-module-level pattern is fragile and should be fixed.

**Affected files:**
- `v2/shared-data/tests/unit/test_agent_self_diagnosis.py`
- `v2/shared-data/tests/unit/test_collect_learning.py`
- `v2/shared-data/tests/unit/test_trade_reviewer.py`
- `v2/shared-data/tests/integration/test_collect_to_dashboard.py`
- `v2/shared-data/tests/integration/test_hook_flow.py`
- `v2/shared-data/tests/integration/test_schema_assertions.py`

**Fix:** Move shared schema-assertion helpers from `conftest.py` into a
sibling module (e.g. `tests/_helpers.py`) and import from there. Tests that
need fixtures keep using conftest; tests that need the assertion callables
import from the helpers module. Once fixed, drop `--continue-on-collection-errors`
from `.git/hooks/pre-push`.

---
## discord-bot decision (covers B1–B5)

**Category:** architecture / cleanup
**Effort:** 1–2 hr (decision) + 2–4 hr (execution)

The `discord-bot/` container (`trader-discord`) is still deployed but its
user-facing slash commands (`/brief`, `/analyze`, `/emergency_stop`,
`/buy`, `/sell`, reaction-based approval) are Phase-1 stubs. Production
trading runs autonomously via cron (Alpha + Beta), and operator approvals
now live on `#karna-approvals` via KARNA/OpenClaw. Decide:

- **(a) Prune** — remove the stub commands and any unused cogs; keep only
  whatever the bot is genuinely used for today (notification posting,
  if any).
- **(b) Wire through** — implement each stub against the real surfaces
  (halt flag, broker.get_account for `position_pct`, KARNA approvals
  bridge for `/emergency_stop`).

The decision blocks B1–B5 because each TODO is half of a Phase-2
integration that was never finished.

### B3 — `/emergency_stop` ⚠ trading-safety

Whichever direction the decision lands, **B3 must land first or be
removed first**. A Discord command that *says* it halted trading but
performs no halt action is a safety footgun: anyone firing it during a
live event would believe the pipeline was stopped while trades kept
flowing.

## Anthropic low-balance alert (B6)

**Category:** observability
**Effort:** 1 hr

Add a pre-emptive Discord alert when Anthropic credit balance drops
under a threshold. Today the failure mode is silent: the pipeline blows
up mid-cycle on a 402/insufficient-credits when the balance hits zero.
A polled check (every 15 min, hooked into existing `collect_*` cadence)
posting to `#system-health` when balance < $X would surface it earlier.

## DST cron rollover — Nov 1, 2026 (from B7 / architecture.md)

**Category:** ops
**Effort:** 15 min on the day, plus a calendar reminder

VPS cron is in UTC. Today's `*/15 13-20 * * 1-5` aligns with 9 ET–16 ET
during DST. When DST ends on Nov 1, 2026 (US clocks fall back), ET
becomes UTC-5 and every market-hours cron line needs the hour range
shifted from `13-20` to `14-21`. Files: every cron entry under
`# Alpha pipeline`, `# Agent Beta`, `# Monitoring`, `# Dashboard
collectors`, `# Auto-Heal` in `CLAUDE.md` and the live `crontab -l`.

A longer-term fix (deferred) is in `docs/architecture.md:1929` —
migrate from cron to systemd timers, which use local time and avoid
DST drift entirely.
