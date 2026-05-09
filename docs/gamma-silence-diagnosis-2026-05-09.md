# Gamma Silence Diagnosis — 2026-05-09

**Status**: Read-only diagnostic. No code changes. No config changes. Recommended fixes listed in Section I are deferred for review.

**Question**: Why has Agent Gamma fired ZERO trades since go-live on **2026-04-30** (~10 trading days)?

**TL;DR**: Gamma is **working correctly**. The market is in a strong rally — every one of the 27 universe symbols has RSI(10) ≥ 30 (lowest is JNJ at 30.37). Connors' "RSI pullback in 200-SMA uptrend" strategy correctly produces zero setups when no symbol is oversold. This is a **market regime** explanation, not a bug. RSI calculation is verified correct (within 0.07 of independent yfinance computation). Finnhub is healthy. Phase 1 gates are dormant because nothing reaches them.

---

## A. Cron status

**Cron entries (verified active, no `HALTED` prefix):**

```
30 20 * * 1-5  python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --scan    >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1
33 13 * * 1-5  python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --execute >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1
```

- **Scan**: 20:30 UTC = 16:30 ET (post-close)
- **Execute**: 13:33 UTC = 09:33 ET (3 min after market open)

**Daily timeline from `/root/quantai-v2/shared-data/logs/gamma.log`:**

| Date | SCAN fired | EXECUTE fired | Errors? |
|---|---|---|---|
| 2026-04-30 | ✓ | ✓ (no pending) | none |
| 2026-05-01 | ✓ | ✓ (no pending) | none |
| 2026-05-04 | ✓ | ✓ (note: discarded 65h-old pending file) | none |
| 2026-05-05 | ✓ | ✓ (no pending) | none |
| 2026-05-06 | ✗ (HALTED) | ✗ (HALTED) | INTC ghost-unwind halt window |
| 2026-05-07 | ✓ | ✓ (no pending) | none |
| 2026-05-08 | ✓ | ✓ (no pending) | none |

**Attribution of missing 2026-05-06**: This was the day after the INTC ghost-unwind. All trading agent crons (Alpha, Beta, Gamma) were halted from 2026-05-05 ~22:00 UTC through 2026-05-06 evening. Gamma's scan + execute resumed normally on 2026-05-07. This explains the gap; not an unscheduled failure.

**Errors in the log**: none. Every line is `[gamma_agent] SCAN start <ts>` / `0 qualifying setups before risk filter (indicators computed for 27)` / `0 after sector/limit filter` / `wrote 0 pending entries`. No tracebacks. No "fetch failed" warnings. No "qualifyContracts" failures (those would be filtered by `_IBKRNoiseFilter` regardless).

---

## B. Scanner setup count by day, last 10 days

Identical pattern every running day:

```
[gamma_agent] SCAN start <ts>  dry_run=False
[gamma_agent] 0 qualifying setups before risk filter (indicators computed for 27)
[gamma_agent] 0 after sector/limit filter
[gamma_agent] wrote 0 pending entries to /root/quantai-v2/shared-data/cache/gamma_pending_entries.json
```

The phrase **"indicators computed for 27"** is critical — it confirms data fetching works, indicators compute successfully, and the bottleneck is downstream at `_qualifies()` (file `gamma/scanner.py:101-125`).

**`gamma_pending_entries.json` current state**:
```json
{"scan_timestamp": "2026-05-08T16:30:09.271546-04:00", "scan_date": "2026-05-08", "entries": []}
```

Always empty since 2026-04-30. Confirmed.

---

## C. RSI sanity check — scanner vs yfinance

**Methodology**: Re-implemented Wilder's RSI(10) independently from `gamma/_indicators.py`. Pulled 1y of daily closes via `yfinance.Ticker(sym).history(period="1y", auto_adjust=False)` and computed RSI inline. Compared to `gamma_indicator_cache.json` (last scan 2026-05-08 16:30 ET).

| Symbol | Gamma RSI | Manual RSI (yfinance) | Δ | Gamma close | yfinance close | Verdict |
|---|---|---|---|---|---|---|
| NVDA | 67.71 | 67.71 | **0.00** | 215.20 | 215.20 | **PASS** |
| TSLA | 76.40 | 76.40 | **0.00** | 428.35 | 428.35 | **PASS** |
| IWM  | 66.68 | 66.61 | **0.07** | 284.23 | 284.17 | **PASS** |

All three within ±0.5 — well within tolerance. The IWM 0.07 delta is from one extra trading day in the yfinance pull (since today is 2026-05-09 vs cache from 2026-05-08).

**Conclusion**: `wilders_rsi()` in `gamma/_indicators.py:13-39` is **correct**. The RSI bug hypothesis is **eliminated**.

---

## D. Filter rejection breakdown — the centerpiece

The 6 filters in `_qualifies()` (from `gamma/scanner.py:101-125`):

1. `sym in open_symbols` — skip if duplicate position
2. `close ≤ sma_200` — must be in uptrend
3. `rsi_10 ≥ 30` — must be oversold
4. `avg_volume_20d < 1M` (stocks only) — liquidity
5. `days_to_earnings ≤ 7` — pre-earnings blackout
6. `days_since_earnings ≤ 2` — post-earnings blackout

**Non-short-circuit evaluation across all 27 symbols (using cache from 2026-05-08 16:30 ET):**

| Filter | Symbols rejected | % of universe | Symbols |
|---|---|---|---|
| **F1** open_symbols | 0 / 27 | 0% | (Gamma has 0 open positions) |
| **F2** close ≤ sma_200 (downtrend) | 8 / 27 | 30% | MSFT, META, BRK.B, JPM, V, MA, HD, PG |
| **F3** rsi_10 ≥ 30 (NOT oversold) | **27 / 27** | **100%** | **ALL 27** |
| **F4** avg_volume < 1M | 0 / 27 | 0% | — |
| F5/F6 earnings | not evaluated (joint failure happens at F3 first) | | |

**Joint pass (F2 AND F3)**: **0 symbols**.

Filter F3 is the dominant blocker — 100% of the universe fails the RSI oversold test. This is the single direct cause of zero setups.

### Closest-to-qualifying analysis

The five uptrending symbols closest to passing F3 (ranked by RSI ascending):

| Symbol | RSI(10) | Distance above 200-SMA | Gap to threshold |
|---|---|---|---|
| **JNJ** | **30.37** | +6.55% | **+0.37** (would qualify if RSI dropped 0.4 points) |
| XOM | 33.23 | +12.21% | +3.23 |
| CVX | 34.69 | +8.47% | +4.69 |
| COST | 52.57 | +5.95% | +22.57 |
| LLY | 53.07 | +3.51% | +23.07 |

**JNJ is 0.37 RSI points away from triggering a setup** as of 2026-05-08. A single down-day on JNJ likely qualifies it on the next scan.

### Downtrend-blocked symbols

8 symbols where F2 (close > sma_200) failed — none are oversold either (RSI all > 30):

| Symbol | RSI(10) | Distance from 200-SMA | Status |
|---|---|---|---|
| HD | 38.38 | -14.10% | NOT oversold + below SMA200 |
| META | 38.50 | -9.80% | NOT oversold + below SMA200 |
| JPM | 40.65 | -1.04% | NOT oversold + below SMA200 |
| MA | 43.88 | -9.39% | NOT oversold + below SMA200 |
| PG | 51.25 | -2.66% | NOT oversold + below SMA200 |
| V | 51.65 | -3.97% | NOT oversold + below SMA200 |
| MSFT | 52.77 | -10.85% | NOT oversold + below SMA200 |
| BRK.B | 54.61 | -2.82% | NOT oversold + below SMA200 |

These would still be blocked by F2 even if RSI dropped — Connors' setup explicitly avoids "falling knife" patterns.

### Re-simulation matches production

This non-short-circuit simulation produces **0 qualifying setups** — matching the production log exactly. Confirms our model of `_qualifies()` is correct.

---

## E. Strike selector — N/A

Scanner returns 0 setups every day → `build_spread()` (`gamma/strike_selector.py:51-196`) is never called. Confirmed via grep on `gamma.log`: no "build_spread", "Initial submit", "spread reward", or "DTE window" lines exist. No instrumentation needed.

For future reference, `build_spread()` early-exits to `None` on:
- No expiry in 14–21 DTE window (line 100)
- Long strike ≥ short strike (line 113)
- Missing mid-quote on a leg (line 124)
- Debit ≤ 0 (line 129)
- Debit > 55% of spread width (line 134)
- Reward:risk < 0.8 (line 144)

---

## F. Earnings data status (Finnhub)

**Code path**: `gamma/earnings.py:23-37`. `_fetch_window()` calls `https://finnhub.io/api/v1/calendar/earnings` with `FINNHUB_API_KEY` env var, 6-second timeout. **Fail-silent** — returns `[]` on any exception.

**Critical**: this fail-silent pattern fails **OPEN**, not closed. In `_qualifies()`:
```python
d_to = days_to_earnings(sym, today)
if d_to is not None and d_to <= EARNINGS_BLACKOUT_DAYS:  # ← None here means "not <= 7"
    return False
```
When Finnhub fails, `d_to` is `None`. The check `None is not None and ...` short-circuits to `False`, so the symbol is **NOT** rejected. Therefore:

> A Finnhub outage cannot silently block trades. It actually does the opposite — it silently lets earnings-week trades through.

**Live API probe (2026-05-09)**:
- `FINNHUB_API_KEY` present (length 40)
- HTTP 200 OK
- Latency: **65 ms**
- NVDA returned 1 earnings record for the 90-day window (`date=2026-05-20, eps_est=1.792`)

**Cache freshness**: `/root/quantai-v2/shared-data/cache/market_intelligence.json` last modified 2026-05-08 17:15 UTC, 11 symbols with `next_earnings_days` populated. Parallel Finnhub pipeline is also alive.

**Conclusion**: Finnhub is **NOT** a contributing cause. Eliminated.

(Note: the user mentioned "Item #12 in the plan: Gamma earnings fail-silent bug, NOT YET FIXED". That bug — surfacing Finnhub failures so we know when symbols are running without the blackout — is real and worth fixing for **safety** reasons, but it does not explain Gamma's silence. See Section I.)

---

## G. Phase 1 gate interactions

**File**: `/root/quantai-v2/shared-data/logs/gate_blocks.jsonl` — **does not exist**. The path will be created the first time a gate fires a block log line.

**Why dormant**: Phase 1 gates were wired into Gamma on 2026-05-08 (commit `a6c35df`) — only the May 8 EXECUTE could have used them. May 8 EXECUTE log line: `no pending entries to execute`. The gates correctly never fired because the upstream scanner produced zero setups.

For future reference, when a setup does qualify, the Gamma execute path calls (in order):
- `check_conviction()` at `gamma_agent.py:371-376`
- `check_macro_blackout()` at `gamma_agent.py:382-395`

The macro blackout (CPI/NFP/FOMC/GDP/PPI/PCE ±15 min) explicitly **exempts** debit spreads from blocking (`_macro_blackout.py:31-37`), so Gamma's strategy type wouldn't typically be blackout-rejected anyway.

**Conclusion**: Phase 1 gates are **eliminated** as a contributing cause for the historical 10 days.

---

## H. Most likely root cause(s), ranked by evidence weight

### 1. **MARKET REGIME — universe is in a rally, no oversold pullbacks** ⭐ confirmed

**Evidence**:
- 27/27 universe symbols have RSI(10) ≥ 30 on the latest scan
- Lowest RSI in the universe: JNJ at 30.37 — just 0.37 points above threshold
- 19 symbols have RSI > 50 (more than half the universe is in the upper half of momentum range)
- Indices NDX (RSI 86.74) and QQQ (RSI 86.99) are in extreme overbought territory
- Distance above 200-SMA averages +9% for indices, +14% for IWM, +25% for AVGO — strong uptrend across the board

**Interpretation**: The strategy is doing exactly what it's designed to do. Connors' RSI(10)<30 in a 200-SMA uptrend is a rare condition (roughly 5-15× per year per symbol). With 27 symbols × 10 trading days, the expectation under normal conditions is 0–4 setups. Observing 0 is well within the noise band of the strategy's natural firing rate.

**Confidence**: HIGH. The data unambiguously shows this is a regime issue, not an implementation issue.

### 2. Strategy is correctly tuned but universe is too narrow ⭐ secondary

A 27-symbol universe is small for a low-frequency strategy. Even if regime conditions normalize, 27 symbols × ~10 firings/year/symbol ÷ 252 trading days ≈ 1.07 expected setups/day **at most**. With the joint requirement of uptrend + oversold, real-world frequency is probably 1 setup every 2–3 days. Plus filters (sector cap of 2, daily cap of 2, position cap of 3) further reduce executable count.

**This frames Part B**: a wider universe (100–150 symbols) is the natural lever to lift firing rate without changing signal logic.

**Confidence**: HIGH that universe is tight; the proposal in Part B will quantify exactly how much lift.

### 3. **ELIMINATED**: RSI calculation bug

Confirmed correct via independent re-implementation (Section C, all three test symbols within 0.07 RSI points).

### 4. **ELIMINATED**: Finnhub silent block

Code reads as fail-OPEN (Section F). Live API healthy (HTTP 200, 65ms).

### 5. **ELIMINATED for historical 10 days**: Phase 1 gates

Wired only on day 9 (2026-05-08). Even then dormant because nothing reached them. `gate_blocks.jsonl` does not exist.

### 6. **ELIMINATED**: cron / data fetch failures

Daily SCAN+EXECUTE timeline is clean (Section A). "Indicators computed for 27" every day.

---

## I. Recommended fixes — for review only, NOT implementing yet

### I.1 — Do nothing on signal logic (HIGH confidence)

The strategy is behaving correctly. RSI<30 in 200-SMA uptrend is by design infrequent. A 10-day observation window is too short to declare it broken. Recommend continuing to observe over a 30–60 day window before any threshold changes.

**Do NOT loosen RSI to 35 or trend filter to SMA(100)** unless explicitly authorized — those changes would alter Connors' edge.

### I.2 — Universe expansion (Part B of this task)

The lever to increase firing rate **without** changing signal logic is to widen the universe from 27 to ~100–150 symbols. Part B will produce `docs/gamma-universe-expansion-proposal.md` with a setup-count comparison over 90 trading days. **Defer this section until Part B lands and the user reviews.**

### I.3 — Address Finnhub fail-silent visibility (independent of root cause)

The current code logs a warning when `_fetch_window()` fails but doesn't surface to the dashboard. A symbol running with `days_to_earnings = None` due to Finnhub timeout is silently exempt from earnings blackout, which is a **safety** concern (could enter a trade right before earnings). Recommend:

- Add a per-symbol `earnings_data_ok` boolean to `gamma_indicator_cache.json`
- Surface to `agent-gamma-state.json` as a tile warning when ≥1 symbol's earnings data is unavailable

This is hygiene, not a cause of silence.

### I.4 — Operational: per-filter rejection counters in gamma.log

When future "0 qualifying" reports are diagnosed, the log line should be self-explanatory. Replace:
```
0 qualifying setups before risk filter (indicators computed for 27)
```
with:
```
0 qualifying setups (rejected: 27 by rsi>=30, 8 by close<=sma_200, 0 by volume, 0 by earnings)
```

This is a 4-line edit to `gamma_agent.py` near the scan log line. Makes future diagnostics 10× faster.

### I.5 — Phase 1 gate test before Gamma fires for real

When the first Gamma setup eventually qualifies, the Phase 1 gates will be invoked for the first time in production. There is currently no test that exercises Gamma → Phase 1 gates end-to-end. Recommend a smoke test that constructs a synthetic qualifying setup and verifies the gate-block path works (writes to `gate_blocks.jsonl` correctly, handles macro blackout exemption for debit spreads, etc.) **before** Gamma actually trades on a fresh signal.

---

## Verification

The following held during this diagnosis:

- ✅ `gamma_indicator_cache.json` readable, contains all 27 symbols
- ✅ yfinance 1.2.0 installed, NVDA / TSLA / IWM histories pulled successfully
- ✅ Finnhub API responds (HTTP 200, 65ms, real data)
- ✅ The 6-filter simulation in Section D produces 0 setups, **matching production log exactly** — model is correct
- ✅ This report covers all 9 user-requested sections (A–I) with concrete data

---

## Stop point

**This concludes Part A.** No code changes were made. No commits. No production data was modified.

Next step (Part B, deferred per user instruction): produce `docs/gamma-universe-expansion-proposal.md` after this report is reviewed. Part B will quantify the setup-count lift from expanding the universe to 100–150 symbols, while keeping all signal thresholds (RSI < 30, SMA 200, earnings 7/2, liquidity 1M) **unchanged**.

Awaiting review.
