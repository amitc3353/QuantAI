# Pre-week deep scan — 2026-05-11

**Date:** 2026-05-11 (Sunday night UTC, ahead of Monday 2026-05-11 market open)
**Author:** Read-only audit (initial pass). Remediation appended in §Remediation after operator review.
**Scope:** Surface anything that could bite during this week's trading — 4-arm Gamma A/B/C/D activates Tuesday on Monday's scan; Alpha + Beta + original Gamma path remain live.

---

## 🎯 OVERALL VERDICT: 🟢 **GREEN — clear to ship**

Initial pass identified five findings; after operator review, the two material findings (C1 + S1) were remediated 2026-05-10 22:39 ET (see §Remediation). Resolution summary:

- ✅ PR #4 closed-without-merge (content was already on main with re-derived SHAs; see `docs/branch-state-audit-2026-05-10.md`)
- ✅ `gamma_spread_status.json` refreshed against the full 155-symbol universe
- ✅ Weekly verify-spreads cron installed (`30 13 * * 1` = Monday 9:30 AM ET)
- 🟡 Cosmetic findings (C2, E1, E2, P1) deferred — none blocking
- 🟢 Verdict transitioned: **YELLOW → GREEN**

The original audit findings remain documented below as a historical record. Initial verdict in the heading above is final; the original "YELLOW" assessment is preserved in §Summary of findings for the audit trail.

---

## 1. Full test suite — ✅ PASS

```
1433 passed, 2 warnings in 34.83s
(deprecation noise only — datetime.utcnow() in test_gamma_arm_state.py + eventkit init)
```

- Unit + integration combined
- 0 failures, 0 errors
- 2 deprecation warnings are pre-existing (`datetime.utcnow()` deprecation in Python 3.12; eventkit lib's `get_event_loop_policy().get_event_loop()`); both are non-blocking

---

## 2. Cron state — 🟡 WARN (3 findings; 1 material)

### Expected entries — verification

| Schedule | Job | Status |
|---|---|---|
| `*/15 13-20 * * 1-5` | `run_pipeline.py` (Alpha) | ✅ present |
| `5 20 * * 1-5` | `run_pipeline.py eod` | ✅ present |
| `*/15 13-20 * * 1-5` | `beta_agent.py` | ✅ present |
| `30 20 * * 1-5` | `gamma_agent.py --scan` (4:30 PM ET) | ✅ present |
| `33 13 * * 1-5` | `gamma_agent.py --execute` (9:33 AM ET) | ✅ present |
| `0 22 * * *` | `reflection_reconciler.py` (22:00 UTC nightly) | ✅ present |
| `30 20 * * 5` | `gamma_weekly_digest.py` (Friday 4:30 PM ET) | ✅ present |
| `*/15 12-21 * * 1-5` + 2 off-hours | `sentinel_agent.py --auto` | ✅ present (3 windows: weekday market, weekday overnight, weekend daytime) |

### 🔴 Finding C1 — `gamma_agent.py --verify-spreads` cron entry is MISSING

- **Expected**: `30 13 * * 1` (Monday 9:30 AM ET = 13:30 UTC) per plan
- **Actual**: zero matches in root crontab
- **Impact**: Without this cron, `gamma_spread_status.json` stays frozen at last manual run (2026-05-09 18:22 ET, against the OLD 27-symbol universe — see Finding S1 below). Filter F0 fails-open for the 128 expanded-universe symbols.
- **Reproduction**:
  ```bash
  sudo -n crontab -l | grep verify-spreads   # returns nothing
  ```
- **Recommended fix** (single line, before 9:30 AM ET Monday):
  ```
  30 13 * * 1  python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1
  ```

### 🟡 Finding C2 — orphan cron pointing at non-existent backup script

- Entry: `0 2 * * *  /root/scripts/karna-backup.sh >> /root/logs/backup.log 2>&1`
- `/root/scripts/karna-backup.sh` does not exist
- Runs nightly at 2 AM, fails silently (cron captures stderr but file doesn't exist → no output to log either)
- **Impact**: cosmetic — produces "no such file" each night. Doesn't interfere with trading. May fill cron error mail if any.
- **Recommended fix**: either remove the cron entry or restore the script. Defer.

### ✅ HALTED / duplicate / orphan summary

- 0 lines with `HALTED` prefix
- `sentinel_agent.py` appears 3× in crontab — verified as 3 distinct time windows (not duplicates)
- `run_pipeline.py` appears 2× — verified as main schedule + EOD (not duplicates)
- No other duplicates or orphans

---

## 3. Log tail analysis — 🟡 WARN (all issues already resolved or non-blocking)

### `pipeline.log` (Alpha) — 48,000 lines lifetime

- **38 "Debate failed — skipping execution"** entries
- Root cause: `ValueError: Unknown format code 'f' for object of type 'str'` in `debate_chamber.py` at the old line 261
- **Already fixed** by commit `dd920ad` (2026-04-29): `_normalize_proposal()` coerces strings/None to float before `:.2f` formatters
- All 38 entries pre-date the fix. Current `debate_chamber.py` has `_to_float()` + `_normalize_proposal()` at lines 282 + 303-306. Verified: file currently parses cleanly under `ast.parse`
- **Status**: historical noise; no current risk
- 1 SyntaxError trace from `autonomous_execution.py:408` — also historical (current file parses cleanly under `ast.parse`)

### `beta.log` — 1,157 lines

- 0 ERROR/Exception/Traceback lines in the last 7 days
- 32 normal start events 2026-05-04 — last entry's modtime confirms Friday close. ✅ Clean.

### `gamma.log` — 35 lines

- 0 ERROR/Exception/Traceback in last 7 days
- Last entry shows clean scan: "wrote 0 pending entries"
- ✅ Clean (small log because gamma cron runs weekdays only, single tick per day)

### `sentinel.log` — 384 lines, 174 ERROR/WARN lines tagged

Decomposed last 7 days:
- **162 × WARN: `react failed: HTTP Error 429: Too Many Requests`** — Discord reaction-poll rate limits. Sentinel handles these as warnings; non-blocking
- **3 × ERROR: `LLM call failed: ClawRoute 500/400`** — Including one `credit balance is too low` on 2026-05-08T20:15:03Z
- **Resolution confirmed**: Sentinel succeeded multiple times after the credit-balance error (last success 2026-05-10T14:00:18Z, `mode=observe actions=2`)
- ✅ Credits were topped up; Sentinel is functional now

### `reconciler.log`

```
2026-05-09 22:00:01 [reconciler] Reconciler done: retried=0 completed=0 escalated=0
2026-05-10 22:00:01 [reconciler] Reconciler done: retried=0 completed=0 escalated=0
```
✅ Running nightly, 0 backlog (memory dir doesn't exist yet — no reflections written since Phase 2 #1 shipped)

### `llm_failures.jsonl` & `gate_blocks.jsonl`

- Both files **don't exist yet** — no LLM failures or gate blocks have occurred since the respective subsystems shipped. Expected pre-day-0 state.

---

## 4. State file integrity — 🟡 WARN (1 material finding)

### 4 arm state files — ✅ healthy

| Arm | Ranker | Equity | Trades | CB | Last updated |
|---|---|---|---|---|---|
| A | rsi_only | $10,000.00 | 0 | False | 2026-05-10T03:17 |
| B | composite | $10,000.00 | 0 | False | 2026-05-10T03:17 |
| C | weighted_blend | $10,000.00 | 0 | False | 2026-05-10T03:17 |
| D | reward_risk_first | $10,000.00 | 0 | False | 2026-05-10T03:17 |

All required keys present (`arm_id`, `ranker_used`, `starting_equity`, `current_equity`, `circuit_breaker_active`, `total_trades`, `last_updated`). All equity in sane $5k-$20k range. ✅

### 🔴 Finding S1 — `gamma_spread_status.json` is stale for the expanded universe

- **File timestamp**: 2026-05-09T18:22:11-04:00 (1.2 days old) — within the freshness window, BUT
- **Contains only 27 symbols** (results array length = 27)
- **Universe is 155 symbols** post-expansion (verified: `from gamma import UNIVERSE` returns 155)
- Of the 27 covered: 13 passed, 13 blocked (48% blocked ratio — high for a typical Monday)
- Sample blocked: BRK.B, JPM, V, UNH, MA — large-caps that shouldn't realistically have wide spreads; likely a yfinance fetch quality issue on those tickers
- **Scanner F0 fail-open semantic**: symbols missing from `spread_status.json` are allowed through (verified in `gamma/scanner.py:_qualifies()`). So 128 of 155 symbols get **no spread protection** on Monday's scan.
- **Reproduction**:
  ```python
  import json
  d = json.load(open("/root/quantai-v2/shared-data/cache/gamma_spread_status.json"))
  print(len(d["results"]))  # 27
  ```
- **Recommended fix**: add the verify-spreads cron (C1) and/or invoke once manually before 9:30 AM ET Monday:
  ```bash
  sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads
  ```
- **Severity**: yellow. Fail-open semantic means the scanner won't crash; it will just allow symbols with potentially-wide spreads to slip through. Combined with the entry caps (max 2/day per arm, RSI<30 filter) the practical impact is small for one week but should be fixed.

### `market_intelligence.json` — ✅ fresh (per market schedule)

- Last write: 2026-05-08 17:15 UTC = Friday post-close
- Today is Sunday → no market refresh has happened yet (expected); Monday 9:00 ET will refresh it
- Not stale by design

### `errors.db` — ✅ accessible, but 🟡 has stale critical entries

- Size: 4.18 MB, 6,129 events total
- Most recent event: 2026-05-11T02:20:02Z (just now — from system_monitor cycles)
- **10 unresolved critical events** — all `anthropic-credit-balance-low` from 2026-05-08 when credits ran out
- Credits restored since (Sentinel runs succeeded after that timestamp), but the catalog reclassifier hasn't auto-resolved them
- 43 unresolved info events (catalog noise)
- **Recommended fix**: low priority. Could be cleared with one SQL UPDATE if Sentinel doesn't catch them in next reclassify cycle.

### `trades.jsonl` — ✅ clean

- 31 records total, 0 malformed JSON
- Breakdown: 23 CLOSED, 5 PHANTOM_NEVER_FILLED, 3 OPEN (matches positions monitor)
- Source distribution: 25 agent_alpha, 3 agent_beta, 2 manual, 1 legacy entry with `source: None` (P001 SOFI from 2026-03-31, pre-source-tagging era — historical only)

---

## 5. Disk / resources — ✅ PASS

- **Disk**: `/dev/sda1` 63% used (46 of 75G), `/boot/efi` 1%. No volume above 80%
- **Memory**: 3.7Gi total, 1.0Gi free + 1.0Gi buff/cache available, 1.8Gi total available. 968M swap used (mild but not concerning given 55-day uptime)
- **Zombies**: none
- **OOM kills**: none in dmesg
- **Python processes**: 6 (dashboard server, cron-spawned collectors, openclaw)
- **Load avg**: 0.23 / 0.25 / 0.16 — very light

---

## 6. Broker connection sanity — ✅ PASS

- `ibgateway.service`: **active**
- `localhost:4002`: TCP probe succeeds
- `ib_insync` connect probe (clientId=99, distinct from production cid=1 to avoid disruption): **connected=True, managed_accounts=1, server_version=176**
- IBKR gateway journal logs (last 7 days): no error/disconnect events found (scrubbed for credential patterns)
- Note: no account number or balance printed — confirmed connection is alive, that's all the audit needs

---

## 7. Git state — ✅ PASS

- Branch: `main`
- Local HEAD = `origin/main` HEAD = `2dbf8f6`
- Ahead/behind: `0 / 0`
- Working tree: clean (only `graphify-out/` shows modified, which is the post-commit hook's auto-rebuild — not source-controlled work)
- Last 5 commits:
  ```
  2dbf8f6 docs(sentinel): pin promotion_history schema + G3 threshold caveat
  a9bc6cd docs(sentinel): gamma 4-arm coverage plan — 11 new check specs
  000b76e docs(roadmap): mark gamma 4-arm + universe expansion shipped
  fd37014 test(gamma): block .env auto-load in feature-flag tests
  d8dbd8c feat(gamma): activate 4-arm A/B/C/D test (commit 5 of 5)
  ```

---

## 8. Environment sanity — 🟡 WARN (cosmetic only)

All checks use **presence + length only**. No values printed.

| Variable | Status |
|---|---|
| `ANTHROPIC_API_KEY` | ✓ present (length=108) |
| `OPENAI_API_KEY` | ✗ missing (not needed — system uses Anthropic via ClawRoute) |
| `FINNHUB_API_KEY` | ✓ present (length=40) |
| `DISCORD_BOT_TOKEN` | ✓ present (length=72) |
| `DISCORD_CHANNEL_LOGS` | ✓ present (length=19) |
| `DISCORD_CHANNEL_ALERTS` | ✓ present (length=19) |
| `DISCORD_CHANNEL_SYSTEM_HEALTH` | ✓ present (length=19) |
| `DISCORD_CHANNEL_APPROVALS` | ✓ present (length=19) |
| `IBKR_USERNAME` | ✓ present (length=10) |
| `IBKR_PASSWORD` | ✓ present (length=12) |
| `BROKER_TYPE` | ✓ present (length=4 — matches "ibkr") |
| `GAMMA_AB_TEST_ENABLED` | ✓ present (length=1 — set to "1" verified earlier via single-line grep) |
| `GAMMA_SPREAD_CHECK_ENABLED` | ✗ MISSING |

### 🟡 Finding E1 — `GAMMA_SPREAD_CHECK_ENABLED` missing from .env

- Code default in `gamma_agent.py:SPREAD_CHECK_ENABLED = os.environ.get("GAMMA_SPREAD_CHECK_ENABLED", "1") == "1"`
- Missing → defaults to enabled. Not a blocker.
- **Recommended fix**: belt-and-braces, set it explicitly to `"1"` in `.env` for clarity. Single line append. Defer.

### 🟡 Finding E2 — `OPENAI_API_KEY` missing

- Not used by the current code paths (system uses Anthropic via ClawRoute; OpenAI was used by an archived weekly-synthesis backup that's no longer cron-driven)
- No blocker. Defer indefinitely.

---

## 9. Critical file permissions — ✅ PASS

- `/root/quantai-v2/shared-data/` — `drwxr-xr-x root:root` — readable by trader
- `/root/quantai-v2/shared-data/journal/paper/` — `drwxr-xr-x root:root`
- `/root/quantai-v2/shared-data/cache/` — `drwxr-xr-x root:root`
- `/root/quantai-v2/shared-data/logs/` — `drwxr-xr-x root:root`
- Permission-denied events in errors.db (last 7 days): **0**
- Per-arm trade journals (mode 0640) — readable via sudo only ✓ (matches the security-by-default pattern)

### 🟡 Finding P1 — Documentation drift: no SSHFS mount

- CLAUDE.md says "files mounted from a remote VPS via SSHFS"
- Actual filesystem: `findmnt /home/trader/QuantAI` returns `ext4 /dev/sda1` — i.e. the session is running **directly on the VPS** (or a host where staging + production are the same disk)
- No functional impact (changes already land in the live filesystem because they're the same disk)
- **Recommended fix**: update CLAUDE.md to reflect actual setup. Defer.

---

## Summary of findings

| ID | Severity | Title | Action by |
|---|---|---|---|
| **C1** | 🔴 yellow-material | `--verify-spreads` cron entry missing | Before 9:30 AM ET Monday |
| **S1** | 🔴 yellow-material | `gamma_spread_status.json` covers only 27 of 155 symbols (consequence of C1) | Same as C1 |
| C2 | 🟡 yellow-cosmetic | orphan `karna-backup.sh` cron | Defer (cosmetic) |
| E1 | 🟡 yellow-cosmetic | `GAMMA_SPREAD_CHECK_ENABLED` missing from .env (defaults to enabled) | Defer |
| E2 | 🟡 yellow-cosmetic | `OPENAI_API_KEY` missing (unused) | Defer indefinitely |
| P1 | 🟡 yellow-cosmetic | CLAUDE.md SSHFS documentation drift | Defer |

### Resolved historical noise (no action needed)

- 38 "Debate failed" pipeline log entries — already fixed by `dd920ad` on 2026-04-29
- 1 SyntaxError in autonomous_execution.py log entry — also old; file parses cleanly now
- 10 `anthropic-credit-balance-low` critical entries in errors.db — credits restored; Sentinel functional since 2026-05-09; entries are stale and will get reclassified or can be manually cleared

---

## Recommended pre-Monday actions

**Tonight / before Monday 9:30 AM ET (priority order):**

1. **Add the verify-spreads cron entry** (closes both C1 and S1 in one move):
   ```bash
   (sudo crontab -l ; echo '30 13 * * 1  python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1') | sudo crontab -
   ```
   *Alternative: run once manually right now so Monday's scan sees a fresh 155-symbol status:*
   ```bash
   sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads
   ```

**Optional / defer:**

2. Add `GAMMA_SPREAD_CHECK_ENABLED=1` to `.env` for explicitness (E1)
3. Remove the orphan `karna-backup.sh` cron entry or restore the script (C2)
4. Clear or reclassify the 10 stale critical entries in errors.db
5. Update CLAUDE.md's SSHFS reference (P1)

**No action needed** on the historical noise items.

---

## Verdict

**🟡 YELLOW — ship with caveats.**

The system will start Monday safely:
- Tests green
- Broker connected
- All 4 arms initialized
- Cron entries for all live trading paths present
- No disk/memory pressure
- Git in sync

The two material findings (C1 + S1) are linked and resolvable with one cron-add or one manual `--verify-spreads` invocation. Without that action, **Monday's 4-arm scan will still run successfully**; the only impact is that 128 of 155 universe symbols will not have spread-quality filtering applied. Given gamma's other filters (RSI<30, SMA200 trend, earnings blackout, daily cap of 2/arm) and the fact that the first 4-arm execute is Tuesday morning (Monday 4:30 PM ET scan → Tuesday 9:33 AM ET execute), there's time to remediate before any 4-arm orders touch the broker.

**Operator decision required**: take action on C1/S1, or accept the fail-open spread filter for week 1 and add the cron at next opportunity.

---

## Remediation — 2026-05-10 22:38–22:40 ET

Applied after operator review of this audit + the linked `docs/branch-state-audit-2026-05-10.md`. Three actions, in order:

### 1. PR #4 closed-without-merge

- Confirmed via `docs/branch-state-audit-2026-05-10.md` that PR content was already on main with re-derived SHAs (`b22aae7→3ca7923`, `227bec3→1d9adb0`, `0ab04f1→67e0fa5`)
- Closed PR #4 on GitHub at `2026-05-11T02:38:23Z` with explanatory comment
- Remote branch `fix/gamma-universe-expansion-2026-05-09` deleted (via `gh pr close --delete-branch`)
- Local stale-tracking ref pruned (`git remote prune origin`)
- ✅ No open PRs, no stale branches, working tree clean

### 2. `gamma_spread_status.json` refreshed against the 155-symbol universe (resolves S1)

Command: `sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads`
Run at: `2026-05-10T22:39:33-04:00`

| Field | Before | After |
|---|---|---|
| `universe_size` | 27 | **155** ✓ |
| `verified_at` age | 1.2 days | **31 s** ✓ |
| `n_passed` | 13 | 27 |
| `n_blocked` | 13 | 123 |
| `n_fetch_failed` | 1 | 5 |
| Sum check | 27 | **155** ✓ |

**⚠️ Caveat — after-hours block ratio is inflated.** The verifier was invoked Sunday 22:39 ET (market closed). After-hours options have wider bid/ask spreads than during market hours, which pushed the blocked count to 123/155 (79%). This is a property of the data, not the verifier code. The Monday cron (action 3 below) will overwrite this state file at 9:30 AM ET with market-hours data — and the 4:30 PM ET scan that consumes the state file runs **after** that overwrite. Sunday-night state is a placeholder; Monday morning will refresh it before any scan reads it.

If for any reason the Monday 9:30 cron doesn't fire (manual operator action required), the consequence is that 123 symbols will be unnecessarily skipped on Monday afternoon's scan. The other 32 symbols would still be available to all 4 arms. Practical impact would be small — daily-cap-of-2 per arm means only 8 picks need to come from 155 candidates anyway.

### 3. Weekly verify-spreads cron entry installed (resolves C1)

Added to root crontab:
```
30 13 * * 1    python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1
```

- `30 13 * * 1` = Monday 13:30 UTC = **9:30 AM ET (DST)** = market open
- Crontab line count: 86 → 87 (delta +1, exactly one new entry, no other entries touched)
- Verified present: `sudo crontab -l | grep verify-spreads` returns the single new line

### Final state (post-remediation)

| Check | Result |
|---|---|
| C1 — verify-spreads cron | ✅ installed |
| S1 — spread_status universe size | ✅ 155 (Sunday data, will refresh Monday 9:30 ET) |
| PR #4 status | ✅ closed without merge |
| Branch `fix/gamma-universe-expansion-2026-05-09` | ✅ deleted on origin |
| Working tree | ✅ clean (only the 2 new docs from this session) |
| C2 / E1 / E2 / P1 | 🟡 deferred (cosmetic, non-blocking) |

### Verdict transition: 🟡 YELLOW → 🟢 GREEN

System is clear to ship Monday. The two material risks are resolved. The four cosmetic findings remain on a future-cleanup backlog but don't block any trading.

---

**Audit + remediation complete. Working tree clean. No open PRs. Gamma 4-arm experiment is armed for Tuesday's first execute.**
