# Intel Decoupling: market_intelligence.py Standalone Cron

**Date:** 2026-05-18
**Status:** Option A shipped. Option B deferred.

## The Bug

`market_intelligence.py` was called exclusively from `run_pipeline.py`
line 215, inside `run_entry()`. The Alpha kill switch (`ALPHA_ENABLED=0`,
added 2026-05-11) gates `run_entry()` at lines 195-208 and returns
**before** the market intelligence refresh. With Alpha paused, the file
was never refreshed.

The Gamma regime gate (`_check_regime_gate()`) reads
`market_intelligence.json` to decide skip/half/normal. It has a 24-hour
staleness fail-open: if the file is >24h old, the gate defaults to
"normal" (full size, no protection). After Alpha was paused, the file
went stale within one day and the regime gate became permanently
decorative — exactly when market-condition protection matters most.

Discovery: pre-restart verification sweep on 2026-05-18 found the file
was 65.5 hours stale.

## Root Cause

`market_intelligence.py` is infrastructure (consumed by Alpha, Beta,
Gamma, debate_chamber, autonomous_execution, pre_trade_check). It was
coupled to the Alpha pipeline entry path, treating a shared resource as
a per-agent artifact. When the per-agent kill switch was added, nobody
traced that the Alpha entry path was the sole refresh mechanism for a
file consumed by other agents.

## What Shipped (Option A) — 2026-05-18

**Standalone cron line** (kill-switch-independent):

```
*/15 13-20 * * 1-5  python3 /path/to/market_intelligence.py >> .../market_intelligence.log 2>&1
```

- Same schedule as Alpha pipeline: every 15 min during market hours
- `market_intelligence.py` has a built-in 90-minute freshness skip
  (exits in <1s if the file is <90 min old), so most cron invocations
  are no-ops
- Zero code changes to `run_pipeline.py` — the existing `run_script()`
  call at line 215 also hits the 90-min skip and becomes a harmless
  no-op when the standalone cron already refreshed

**Freshness math at gamma scan time:**

| Cron | Schedule (UTC) | Purpose |
|------|----------------|---------|
| Intel refresh | `*/15 13-20` | Last fires at 20:00 |
| Gamma --scan | `30 20` | Reads file at 20:30 |

Worst case: file refreshed at 20:00, read at 20:30 = **30 minutes old**.
Comfortably under the 24-hour staleness threshold.

## What Is Deferred (Option B) — Correct End-State

Remove the `run_script("market_intelligence.py")` call from
`run_pipeline.py` `run_entry()` entirely. The pipeline would read the
cached file like all other consumers (Beta, Gamma, debate_chamber).

Why this is better:
- Eliminates the latent coupling that caused THIS bug
- All consumers treat the file as read-only cache; one standalone
  producer owns the refresh
- Matches the position_monitor pattern: infrastructure runs independent
  of per-agent kill switches

Why it's deferred:
- Option A is sufficient and zero-risk (no code change)
- Option B is a small code change but should be done in a proper window
  with test verification, not bolted onto the regime gate work
- Tracked here so it doesn't get silently tolerated forever

## All Consumers of market_intelligence.json

| File | Critical? | Own Staleness Check? |
|------|-----------|----------------------|
| run_pipeline.py (Alpha) | Yes | No (it's the producer) |
| debate_chamber.py | Yes | No (hard-fails if missing) |
| autonomous_execution.py | Yes | Yes — `_freshness_gate`: 20-min (5-min for events) |
| gamma_agent.py | Yes | Yes — 24h staleness → fail-open |
| beta_agent.py | Yes | No (hard-fails if missing) |
| pre_trade_check.py | No | Warns if >18h old |
| system_test.py | No | Reports age |

## Restart Gate

The 4-arm experiment restart is gated on intel decoupling running clean
for several days. The standalone cron must produce fresh files reliably
before the restart proceeds. Gamma scans that fire between now and
restart benefit from the regime gate reading real data instead of
fail-open on stale.
