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
## discord-bot pruned 2026-05-06 (B1–B5 resolved)

**Category:** architecture / cleanup
**Status:** Done — see commit `chore: prune legacy discord-bot ...`

The `discord-bot/` container (`trader-discord`) carried five Phase-1 stub
TODOs from the original audit:

- **B1** `/brief` slash command — `bot.py:217`, "trigger Research Agent"
- **B2** `/analyze` slash command — `bot.py:238`, "trigger Analysis Agent"
- **B3** `/emergency_stop` slash command — `bot.py:292`, no halt action
  performed (trading-safety footgun)
- **B4** Reaction-based trade approval — `bot.py:396`, "Forward to
  execution agent"
- **B5** `cogs/trading.py:245` — hardcoded `position_pct = 2.0` sent to
  guard engine instead of real portfolio share

Investigation on 2026-05-06:

- Zero functional events in the prior 30 days (only Discord-gateway
  RESUME heartbeats).
- No cron, no systemd, no external code path called into the bot.
- Production Discord traffic flows through OpenClaw and direct webhook
  posts (different env-var namespace: `DISCORD_CHANNEL_*` vs the bot's
  `CHANNEL_*`), not through this bot.
- `infra_agent.py` cog mounted `/var/run/docker.sock` and could restart
  sibling containers — meaningful blast radius for code that was inert.

**What was removed**

- Running container (`docker stop trader-discord && docker rm`).
- `discord-bot:` block in `docker-compose.yml` — commented out, not
  deleted, with a header pointing to this BACKLOG entry.

**What was retained**

- Full source tree under `discord-bot/` — preserves git history and
  keeps the `ruff` step in `.github/workflows/ci.yml:47` working.
- `configs/channels.json` — channel IDs still listed; harmless.
- The Docker image is preserved (only the running container was
  removed).

**Reversal**

Uncomment the `discord-bot:` block in `docker-compose.yml` and run
`docker compose up -d discord-bot`. Then re-decide between options
(a) prune-the-stubs and (b) wire-through, both of which are still
open if the bot is brought back.

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
