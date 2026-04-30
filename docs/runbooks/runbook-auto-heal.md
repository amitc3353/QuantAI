# Runbook: Auto-Heal Routine

**Purpose.** Claude-driven triage that runs 4×/day around the trading window. Diagnoses issues, auto-applies safe fixes, and queues risky fixes for one-tap ✅ approval on Discord.

**Sits on top of**, does not replace: `error_detector.py` (rules, every 5min), `heartbeat_monitor.py`, `position_monitor.py`, `error_learner.py` (weekly).

## Schedule (UTC)

| Slot | Mode | Why |
|------|------|-----|
| `30 12 * * 1-5` | apply | 30 min before pipeline first fires (13:00) — fix overnight breakage |
| `0 15 * * 1-5` | observe | Mid-trading; queues findings to digest, never mutates |
| `0 18 * * 1-5` | observe | Mid-afternoon; queues findings to digest, never mutates |
| `45 20 * * 1-5` | apply | 25 min after EOD wrap (5 20) and 15 min after gamma scan (30 20) |

Apply mode re-asserts the trading-window guard at runtime — if it ever fires inside 13–20 UTC weekday it auto-downgrades to observe.

## What it does

**observe (15:00 / 18:00 UTC)** — read-only:
- Bundles dashboard state, log error-grep tails, heartbeat ages, error catalog taxonomy, IBKR `is-active` exit code.
- Single Claude call returns `{summary, findings, proposals}`.
- Writes `propose_wait` proposals to `auto_heal_data/pending_fixes/` and posts approval cards to `#karna-approvals` (Discord channel env: `DISCORD_CHANNEL_APPROVALS`, falls back to `DISCORD_CHANNEL_ALERTS`).
- Appends a row to `auto_heal_data/digest_buffer/<utc_date>.jsonl` for the post-close digest.
- **Does not post per-run status to Discord during trading hours** (low noise by design).

**apply (12:30 / 20:45 UTC)** — can mutate:
- Runs the same diagnose loop (same Claude call) so proposals are fresh.
- Iterates `pending_fixes/`. For each:
  - `safe_auto` class → executes immediately.
  - `propose_wait` class → polls Discord for ✅ from a non-bot user; executes if approved.
  - Expired (>48h) → deletes.
  - Quarantined (3 failed attempts) → skipped, posts escalation to `#system-health`.
- Writes `auto_heal_data/applied/<fix_id>.json` receipts with `.bak` paths.
- The 12:30 run posts a one-line "pre-market" status card to `#system-health`.
- The 20:45 run drains `digest_buffer/<utc_date>.jsonl` and posts the **daily digest** to `#system-health`.
- Writes `/var/dashboard/state/quantai-auto-heal.json` so the dashboard tile shows pending count and quarantined ids.

## Hard safety rails (enforced in Python, not LLM-overrideable)

| Rule | Where |
|------|-------|
| Trading-window guard at runtime | `is_trading_window()` in apply path |
| Lock file (`/tmp/auto_heal.lock`) prevents overlap | `RunLock` |
| `.bak.YYYY-MM-DD-HHMMSS-autoheal` before any edit | `apply_diff()` |
| Path allowlist: `v2/shared-data/scripts/`, `docs/`, `/var/dashboard/state/` only | `is_path_allowed()` |
| Never-touch: `.env`, openclaw, ibgateway service, journal mutations, anything matching `(secret\|token\|credential\|api[_-]?key\|password)` | `NEVER_TOUCH_PATHS`, `CREDENTIAL_PATTERNS` |
| Open-positions guard: refuses edits to `broker.py`, `_broker_ibkr.py`, `position_monitor.py`, `autonomous_execution.py`, `beta_agent.py` if any OPEN trade exists | `validate_proposal()` |
| Diff cap 80 lines, 3 file mutations + 2 service restarts per run | `MAX_DIFF_LINES`, `MAX_FILE_MUTATIONS_PER_RUN`, `MAX_SERVICE_RESTARTS_PER_RUN` |
| 3-attempt budget per fix-id, then quarantine | `ATTEMPT_BUDGET`, `state.json` |
| Shell-command safety: rejects `sudo`, `rm -rf /`, `dd`, `mkfs`, `> /dev/`, `curl`, `wget`, `\| sh`, `eval`, `systemctl stop` | `is_command_safe()` |
| IBKR probe is `systemctl is-active` only — never `status` (CLAUDE.md credential leak rule) | `gather_context()` |

## Operator commands

```bash
# Status — list pending fixes, quarantined ids, last-run timestamps
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --status

# Dry-run (no Discord, no LLM cost, no writes)
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --mode=observe --dry-run
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --mode=apply --dry-run

# Real run on demand (use --mode=apply only outside trading window)
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --mode=observe
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --mode=apply

# Rollback a fix that turned out wrong (restores from .bak.*-autoheal)
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --rollback <fix_id>

# Reset quarantine for a fix-id (after manual intervention)
python3 /home/trader/QuantAI/v2/shared-data/scripts/auto_heal.py --reset <fix_id>
```

## Discord channels and approval flow

Channel env vars (set in `.env`; all fall back to `DISCORD_CHANNEL_ALERTS` if unset):

- `DISCORD_CHANNEL_SYSTEM_HEALTH` — per-run status cards, daily digest, escalations
- `DISCORD_CHANNEL_APPROVALS` — proposed fixes with ✅/❌ buttons
- `DISCORD_CHANNEL_LOGS` — per-fix execution receipts

**To approve a fix from your phone:** open Discord → `#karna-approvals` → tap the ✅ that the bot pre-reacted with. The next 12:30 or 20:45 UTC apply run picks it up.

**To dismiss:** tap ❌. The proposal stays in `pending_fixes/` until expiry (48h) but won't be applied.

## Mobile UX

- **Discord** is the primary surface — works on iOS/Android.
- **Dashboard tile** at `https://quantai.tail1465ff.ts.net/` shows `pending_count`, `pending_ids`, `quarantined`, `last_run` timestamps. Tile data lives at `/var/dashboard/state/quantai-auto-heal.json` and is rewritten by `auto_heal.py` itself at the end of every run (no separate collector needed).

## What gets queued vs. what auto-runs

| Class | Examples | Approval needed? |
|-------|----------|------------------|
| `safe_auto` | Truncate stale `/tmp/*.lock`, restart non-trading services, retry an `auto_action=retry` catalog entry the rule-based detector missed | No — runs at next apply slot |
| `propose_wait` | Code edits, novel/unknown errors, anything where Claude is <90% sure | Yes — ✅ on Discord |
| `never_touch` | `.env`, openclaw, ibgateway, journal, paths outside repo | Never — observed only |

## Cost

≈ 5k input + 1–2k output tokens per Claude call × 4 runs/day × 22 trading days ≈ **$4–5/month** at Sonnet pricing via ClawRoute. Budget tracking surfaces under existing `collect_clawroute.py`.

## Failure modes & first response

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `auto_heal.log` shows "ClawRoute …" | ClawRoute down | Wait for next slot; or set `LLM_BYPASS_CLAWROUTE=1` env to fall back to direct Anthropic API |
| Discord posts not appearing | `DISCORD_BOT_TOKEN` or channel id missing | `--status` doesn't check this; check `.env`. Script logs "WARN: discord skipped" |
| `pending_count` growing without bound in tile | Approvals not coming through | Open Discord, ✅ or ❌ each card |
| Fix quarantined after 3 fails | Underlying issue not what Claude thought | Inspect `applied/<fix_id>.json` for the receipt; manually fix the root cause; `--reset <fix_id>` |
| LLM returned non-JSON | Prompt drift / rate limit | One-off; next run re-tries. If persistent, re-read `SYSTEM_PROMPT` in `auto_heal.py` |
| `gather_context` times out on `systemctl is-active` | Process load on VPS | Increase the 5s timeout in `gather_context()` |

## Verification before the first real apply slot

1. Run `--status` — expect `pending: 1` (the canary).
2. Run `--mode=observe --dry-run` — expect a clean exit, no Discord posts, no writes.
3. Run `--mode=apply --dry-run` — expect the canary to "consume" in dry mode.
4. Test rollback machinery on a throwaway file (create `/tmp/rb_test`, copy a `.bak`, run `--rollback`).
5. **First real apply slot**: the canary fires, exercising the `.bak`/lock/receipt path end-to-end. Receipt lands in `applied/canary.json`; tile pending count drops to 0.

## Files

| Path | Purpose |
|------|---------|
| `v2/shared-data/scripts/auto_heal.py` | Main script |
| `v2/shared-data/scripts/auto_heal_data/pending_fixes/` | Queued proposals (one JSON per fix) |
| `v2/shared-data/scripts/auto_heal_data/applied/` | Execution receipts with `.bak` paths |
| `v2/shared-data/scripts/auto_heal_data/digest_buffer/` | Per-day rollup of observe-run findings |
| `v2/shared-data/scripts/auto_heal_data/state.json` | Per-run timestamps, attempt counters, quarantine list |
| `/tmp/auto_heal.lock` | Run lock (prevents overlap) |
| `/root/quantai-v2/shared-data/logs/auto_heal.log` | Main log (root cron writes here) |
| `/var/dashboard/state/quantai-auto-heal.json` | Dashboard tile data |
| `docs/error-catalog.json` | Read-only — taxonomy reference for `auto_action` field |
