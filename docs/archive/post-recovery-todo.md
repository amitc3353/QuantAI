# Post-Recovery TODO — deferred bugs from the 2026-06-03 error sweep

Surfaced during the Step-2 error sweep of the Gamma incident recovery
(2026-06-01 → 2026-06-03). None block the Gamma restart; all are
pre-existing and off the gamma trading path. Deferred deliberately rather
than rush-fixed before market open.

## P1 — Sentinel reaction-poll 429 storm

**Symptom:** `sentinel.log` shows bursts of `WARN: reaction-poll failed:
HTTP Error 429: Too Many Requests` — 714 total, clustered (e.g. ~24 in the
12:30:33-35Z window 2026-06-03; ~14 in 20:18-19Z 2026-06-02).

**Root cause:** `has_human_reaction()` (sentinel_agent.py:333) makes a single
GET per call with no Retry-After handling. The caller polls it once per
pending-fix message every cycle. When several approval messages are pending,
the bot fires many reaction GETs back-to-back, Discord rate-limits (429), and
every call in the batch fails. No exponential backoff, no respect for the
`Retry-After` header, no dedupe/caching of reaction state within a cycle.

**Fix design (when picked up):**
- Honor the `Retry-After` header on 429 (sleep then one retry).
- Batch/space reaction polls (e.g. 250-500ms between calls) or cache results
  per cycle so the same message isn't re-polled.
- Cap reaction polls per cycle; log once if capped.
- Test: mock urlopen to raise HTTPError(429) with a Retry-After header,
  assert the function backs off and retries rather than failing immediately;
  assert no tight loop.

**Priority P1** because a persistent 429 storm risks Discord rate-limiting or
temp-banning the bot, which would break the approval channel. But it does NOT
affect trading (gamma/FSM/broker), so it's safe to defer past the restart.

## P2 — `qualifyContractsAsync was never awaited` RuntimeWarning

**Symptom:** `gamma.log` and `position_monitor.log` show
`_broker_ibkr.py:440: RuntimeWarning: coroutine 'IB.qualifyContractsAsync'
was never awaited` followed by `return []`.

**Root cause:** in the broker close-poll / order-status path, an ib_insync
async coroutine is created but never awaited before the function returns `[]`.
Functionally benign today (the `[]` return is the correct fallback and callers
handle it), but it leaks a coroutine object each call and muddies the logs.

**Fix design:** trace _broker_ibkr.py:440, ensure the async call is either
properly awaited (via `ib.qualifyContracts` sync wrapper) or not created at
all on that path. Test against the FakeBroker / live paper smoke.

**Priority P2.** Benign, pre-existing, in the broker primitive — deliberately
NOT rush-edited right before market open. Pick up in a calm window.

## P3 — gamma_weekly_digest Discord post 403 Forbidden

**Symptom:** `gamma.log`: `[gamma_weekly_digest] discord post failed: HTTP
Error 403: Forbidden`.

**Root cause:** the weekly digest posts to a Discord channel the bot lacks
permission for (or a stale channel ID / token scope). Non-trading, weekly.

**Fix design:** verify the digest's target channel env var and the bot's
permissions on that channel. Likely a one-line channel-ID correction.

**Priority P3.** Cosmetic — the weekly digest just doesn't post. No trading
impact.

## Not bugs (catalogued so they aren't re-investigated)

- `pipeline` heartbeat stale since 2026-05-29 — Alpha is intentionally paused
  (`ALPHA_ENABLED=0`). Expected. Will clear when Alpha is un-paused.
- `BETA_ENABLED=0 — skipping` in beta.log — intentional pause. Expected.
- lifecycle_shadow.log `EXIT_ACKED` proposals on CLOSED/PHANTOM/OPEN trades
  (2026-06-02 20:58) — incident-era shadow-mode divergence while the 8 trades
  were mid-cascade with `state=EXIT_SUBMITTED` and dual status fields. Resolved
  by the 2026-06-03 final reconciliation: all 8 are now CLOSED/CLOSED
  (terminal), so the FSM short-circuits and won't propose further transitions.
- Fork-bomb / OOM — fixed in a694c89 (pre-push guard) + f934073 (dedupe,
  resurrection guard, dup-id guard). Zero recurrence; historical OOM-kill
  catalog entries can be ignored.

## P2a — Sentinel hallucinating unit names: NOT A BUG (guard works)

Investigated 2026-06-05. Sentinel's `_validate_command_targets` (line 1193)
already validates proposed units via `systemctl list-unit-files` and REJECTS
any hallucinated unit (`return False, "hallucinated systemd unit: ..."`). The
proposal is deleted, Discord is alerted, and it's never executed. The guard
works exactly as designed.

The hallucination itself is an LLM prompt-quality issue (the context fed to
Claude includes stale/incorrect service names for QuantAI cron-based collectors
that don't use systemd). Fixing this requires prompt engineering, not code.

**No code change needed.** If the hallucination volume is bothersome, tune the
prompt context for the "restart stale collector" use-case to list actual cron
jobs instead of fake systemd units. Low priority — the guard prevents execution.

## P2b — KARNA backup: ALREADY WORKING (root cron, not agent-run)

Investigated 2026-06-05. The backup script at `/root/scripts/karna-backup.sh`
runs as a root cron (`0 2 * * *`), NOT from KARNA's openclaw sandbox. Last
successful run: 2026-06-05 02:00:03 UTC — 48 files bundled, encrypted, verified,
pushed to `karna-backups` GitHub repo. Working correctly.

The original concern ("script can't run from KARNA's sandbox") is moot — it
never runs from KARNA's sandbox. The root cron approach is the correct one
(backup needs access to all config files across multiple user directories).

**No fix needed.** system_monitor's `check_karna_backup_freshness` correctly
monitors this via the backup log timestamp.

## P2c — Disk + errors.db: NOT URGENT (68% used, 12MB db)

Investigated 2026-06-05. Disk at 68% (was 81.6% during the OOM incident when
swap + tmpfs were bloated; resolved by killing the fork bomb).

- `/var/log` 4.4G: systemd journals, auto-rotated
- `/var/lib/docker` 2.4G: legacy containers (trader-cto, trader-guards)
- `errors.db` 12MB, 17424 rows, only 10 older than 30 days, 15086 resolved

No rotation policy needed at this size. Docker legacy containers are the only
candidate for space recovery (2.4G) if disk ever approaches 80% again; prune
with `docker system prune -a` when operator confirms no legacy dependency.

## P2d — ClawRoute "500" errors: ANTHROPIC CREDIT EXHAUSTION (operator action)

Investigated 2026-06-05. The errors are **not** ClawRoute 500s — they're
Anthropic API 400 responses: `"Your credit balance is too low to access the
Anthropic API."` LiteLLM (Docker, active, up 2 days) correctly proxies the
upstream error.

**Operator action required:** top up Anthropic API credits at
https://console.anthropic.com/settings/plans. Until credits are restored:
- Sentinel's LLM-driven analysis/proposals are non-functional (retries exhaust,
  falls back to safe-auto-only mode)
- Gamma's debate_chamber (Alpha-only, currently paused) would also fail
- All Python-side LLM calls via _llm_call.py are affected

**No code fix.** The retry logic + fallback behavior is correct. The Sentinel
correctly suppresses proposals when the LLM call chain fails, so no spurious
actions are taken.
