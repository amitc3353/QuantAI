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
