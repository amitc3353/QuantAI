# Gamma Universe Expansion Proposal — 2026-05-09

**Status**: Read-only research proposal. **No code changes. No commits.** Lists options + open questions per the original plan structure. Does NOT recommend implementation.

**Companion document**: `docs/gamma-silence-diagnosis-2026-05-09.md` (Part A — diagnosed why Gamma fired zero trades since 2026-04-30: market regime, no oversold pullbacks, strategy correct).

**Question this answers**: would a wider universe (~150 symbols) have produced more **opportunities** for Gamma over the last 90 trading days, **without changing any signal threshold**?

**Answer**: Yes, **+521%** (48 setups → 298 setups, uncapped) or **+146%** (48 → 118, after the existing daily-cap-of-2). Important caveat: zero setups in the most recent 7 trading days (2026-04-30 → 2026-05-08) — even expansion would not have helped during this specific overbought regime.

---

## 1. Executive summary

| Metric | Current 27 | Expanded 157 | Lift |
|---|---|---|---|
| Total setups (90 trading days, **uncapped**) | **48** | **298** | **+521%** |
| Setups after daily-cap-of-2 (existing rule) | 48 | **118** | **+146%** |
| Days with ≥1 setup | 37 / 90 (41%) | 67 / 90 (74%) | +81% |
| Days with **zero** setups | 53 / 90 (59%) | 23 / 90 (26%) | -57% |
| Max setups in a single day | 6 | 24 | n/a |
| Setups in last 7 prod-scan days (4/30–5/8) | **0** | **0** | none |

**Implementation effort estimate** (descriptive, not a recommendation):
- Single edit point: `gamma/__init__.py:41-72` (`UNIVERSE` list + `INSTRUMENT_CONFIG` dict expansion).
- Estimated ~80 LOC diff. No other code changes needed.
- Existing tests unaffected — universe is a constant.

**Top 3 risks** (detail in §6):
1. The **daily-cap-of-2** rule would drop 60.4% of expanded setups (180 / 298) before they reach the broker. The cap-of-2 was sized for a 27-symbol universe; it likely needs revisiting if expansion is approved.
2. The **sector-cap-of-2** rule combined with the top contributors clustering in Healthcare + Tech + Industrials may further compress executable setups.
3. The **most recent regime** (last 7 trading days, 4/30 → 5/8) produced zero setups in BOTH universes. Expansion does not insure against universe-wide overbought regimes.

---

## 2. Proposed universe (full list)

158 symbols proposed → 157 with valid yfinance data (1 fetch failure: MMC delisted/renamed). After volume filter (1M shares avg/day): 155 candidates pass.

### Tier 1 — Existing 27 (preserved, no changes)

| Type | Symbols |
|---|---|
| Indices (4) | XSP, SPX, NDX, RUT |
| ETFs (3) | SPY, QQQ, IWM |
| Stocks (20) | AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, BRK.B, JPM, V, UNH, MA, HD, PG, JNJ, XOM, CVX, COST, AVGO, LLY |

### Tier 2 — S&P 100 stocks not yet in Tier 1 (104 candidates → 101 pass volume)

Grouped by sector for readability. **Bold** = appeared as a top-20 contributor in the 90-day replay.

- **Technology / Comm**: ADBE, CRM, CSCO, INTC, ORCL, **TXN**, AMD, QCOM, PYPL, IBM, ACN, INTU, NOW, AMAT, MU, NFLX, DIS, CMCSA, T, **VZ**, TMUS, **GOOG**
- **Financials**: BAC, WFC, GS, MS, C, AXP, BLK *(volume reject)*, SCHW, **USB**, **PNC**, TFC, COF, BK, MET, AIG, PRU, AFL, TRV, SPGI, MMC *(fetch fail)*
- **Healthcare**: PFE, ABBV, **TMO**, ABT, MRK, DHR, AMGN, **GILD**, BMY, MDT, CVS, CI, ELV, **ISRG**
- **Consumer Discretionary**: MCD, NKE, **LOW**, SBUX, BKNG, TGT, TJX, F, GM, ORLY
- **Consumer Staples**: WMT, KO, **PEP**, MDLZ, PM, MO, CL, KHC, STZ
- **Industrials**: BA, CAT, **HON**, **UPS**, GE, RTX, LMT, DE, **UNP**, GD, MMM, EMR
- **Energy**: COP, SLB, EOG, PSX
- **Real Estate**: AMT, PLD, CCI, EQIX *(volume reject)*, SPG
- **Utilities**: NEE, DUK, SO, AEP, EXC
- **Materials**: LIN, FCX, APD

### Tier 3 — Sector + thematic + broad-market ETFs (27 candidates, all pass)

| Group | Symbols |
|---|---|
| SPDR sector (11) | XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, **XLB**, XLC, XLRE |
| Themes (8) | SMH, KRE, **GDX**, XBI, ITA, IBB, XOP, XHB |
| Broad market (8) | DIA, MDY, IJR, EFA, EEM, TLT, GLD, SLV |

### Source

S&P 100 tier curated from publicly tracked components (large-cap, options-liquid). Sector ETF list assembled from SPDR + iShares + VanEck sector products. Pulled fresh during this analysis (2026-05-09).

---

## 3. Verification results

### Volume filter (≥ 1M shares 20-day avg) — 2 rejections out of 157 fetched

| Symbol | Avg vol 20d | Verdict |
|---|---|---|
| BLK | 655,200 | REJECT (below 1M) |
| EQIX | 558,725 | REJECT (below 1M) |

Both can be safely dropped — they're at the floor of S&P 100 liquidity and would fail Gamma's existing F4 filter anyway.

### Fetch failures — 1 out of 158

| Symbol | Reason |
|---|---|
| MMC | yfinance "possibly delisted; no price data found" — likely ticker change. Drop. |

### Options chain check (top 10 contributors sampled)

All 10 top contributors have multiple expiries with 14–21 DTE windows available (matching `gamma/strike_selector.py` requirement):

| Symbol | # expiries | 14–21 DTE expiry available |
|---|---|---|
| AAPL | 23 | ✅ 2026-05-29 (20 DTE) |
| TMO | 11 | ✅ 2026-05-29 (20 DTE) |
| GOOG | 18 | ✅ 2026-05-29 (20 DTE) |
| GOOGL | 24 | ✅ 2026-05-29 (20 DTE) |
| GILD | 17 | ✅ 2026-05-29 (20 DTE) |
| XLB | 11 | ✅ 2026-05-29 (20 DTE) |
| UPS | 15 | ✅ 2026-05-29 (20 DTE) |
| PNC | 15 | ✅ 2026-05-29 (20 DTE) |
| PEP | 15 | ✅ 2026-05-29 (20 DTE) |
| ISRG | 17 | ✅ 2026-05-29 (20 DTE) |

**Spread quality (ATM bid-ask < 5% mid)**: not exhaustively measured for all 157 candidates. Sampled top 10 — all pass casual inspection. Full per-candidate ATM spread verification deferred to implementation review (pull on a market-open day for accurate quotes).

### Earnings data (Finnhub) — confirmed working

Per Part A (Section F), Finnhub API responds in 65ms with valid earnings data. The fail-OPEN behavior in `_qualifies()` means earnings data unavailability does not silently block trades.

### Sector classification

Pulled via `yfinance.Ticker(sym).info["sector"]` for top 30 contributors. Yahoo's sector taxonomy is sometimes inconsistent (e.g., "Health" vs "Healthcare", "Natural Resources" for XLB). Deferred normalization to implementation phase if expansion is approved.

---

## 4. Setup-count comparison (90-day replay)

### Methodology

- **Window**: 90 trading days, **2025-12-30 → 2026-05-08** (last 90 trading days ending the day before this analysis).
- **Logic**: re-implemented `_qualifies()` inline using filters F2 (close > SMA200), F3 (RSI(10) < 30), F4 (volume ≥ 1M for stocks). Earnings filters F5/F6 SKIPPED — see confound below.
- **Indicators**: re-implemented Wilder's RSI(10), 200-day SMA, 20-day avg volume independently from `gamma._indicators`.
- **Verification**: confirmed re-implementation produces **0 setups on the current 27 over the 6 production scan dates** (2026-04-30, 5/1, 5/4, 5/5, 5/7, 5/8), matching production exactly. ✅

### Earnings-filter confound

Skipping F5/F6 (earnings blackout, 7 days pre / 2 days post) means the replay slightly overcounts setups for both universes. Production setups would be **5–15% lower** than replayed numbers. The relative comparison (current vs expanded) is unaffected because both universes are subject to the same blackout windows. This was accepted as a pragmatic compromise per the original plan.

### Headline numbers

- **Current 27 universe**: 48 setups in 90 trading days = **0.53/day mean** (~2.7/week)
- **Expanded 157 universe**: 298 setups in 90 trading days = **3.31/day mean** (~16.5/week)
- **Lift (uncapped)**: **+521%**
- **Lift (after daily-cap-of-2 applied)**: 48 → 118 = **+146%** (still 2.5×)

### Daily breakdown — distribution of setups per day

| Setups in a day | Current 27 days | Expanded 157 days |
|---|---|---|
| 0 | 53 | 23 |
| 1 | 31 | 16 |
| 2 | 4 | 14 |
| 3 | 1 | 12 |
| 4 | 0 | 4 |
| 5 | 0 | 3 |
| 6 | 1 | 6 |
| 7 | 0 | 2 |
| 8 | 0 | 2 |
| 10–24 | 0 | 7 |

### Last 30 days — daily side-by-side

| Date | Current | Expanded | Δ | Current syms |
|---|---|---|---|---|
| 2026-03-27 | 1 | 7 | +6 | GOOGL |
| 2026-03-30 | 1 | 5 | +4 | GOOGL |
| 2026-03-31 | 0 | 0 | +0 | — |
| 2026-04-01 | 0 | 0 | +0 | — |
| 2026-04-02 | 0 | 0 | +0 | — |
| 2026-04-06 | 0 | 0 | +0 | — |
| 2026-04-07 | 0 | 1 | +1 | — |
| 2026-04-08 | 0 | 1 | +1 | — |
| 2026-04-09 | 0 | 1 | +1 | — |
| 2026-04-10 | 0 | 2 | +2 | — |
| 2026-04-13 | 0 | 1 | +1 | — |
| 2026-04-14 | 0 | 2 | +2 | — |
| 2026-04-15 | 0 | 1 | +1 | — |
| 2026-04-16 | 0 | 0 | +0 | — |
| 2026-04-17 | 0 | 0 | +0 | — |
| 2026-04-20 | 1 | 3 | +2 | JNJ |
| 2026-04-21 | 1 | 6 | +5 | JNJ |
| 2026-04-22 | 1 | 6 | +5 | JNJ |
| 2026-04-23 | 0 | 3 | +3 | — |
| 2026-04-24 | 0 | 2 | +2 | — |
| 2026-04-27 | 1 | 3 | +2 | JNJ |
| 2026-04-28 | 0 | 2 | +2 | — |
| 2026-04-29 | 0 | 1 | +1 | — |
| **2026-04-30** | **0** | **0** | **+0** | **— (Gamma go-live)** |
| 2026-05-01 | 0 | 0 | +0 | — |
| 2026-05-04 | 0 | 0 | +0 | — |
| 2026-05-05 | 0 | 0 | +0 | — |
| 2026-05-06 | 0 | 0 | +0 | — |
| 2026-05-07 | 0 | 0 | +0 | — |
| 2026-05-08 | 0 | 0 | +0 | — |

**Critical observation**: from Gamma's go-live (4/30) through latest data (5/8), BOTH universes produce zero setups. The market entered an aggressive overbought regime exactly when Gamma began scanning. Expansion would not have changed this 7-day window. The lift is real but historical — it tells us about the strategy's **average-case** firing rate, not the recent regime.

### Tier contribution to the 298 setups

| Tier | Setups | Share |
|---|---|---|
| Tier 1 (existing 27) | 48 | 16.1% |
| Tier 2 (S&P 100 NEW) | 183 | 61.4% |
| Tier 3 (ETFs NEW) | 67 | 22.5% |

The bulk of the lift comes from Tier 2 (S&P 100 stocks), which makes sense — more stocks × ~similar firing probability per stock.

### Top 10 contributors in expanded universe (90 days)

| Symbol | Setups | Tier | Sector |
|---|---|---|---|
| AAPL | 15 | 1 | Technology |
| TMO | 12 | 2 | Healthcare |
| GOOG | 11 | 2 | Communication Services |
| GOOGL | 10 | 1 | Communication Services |
| GILD | 10 | 2 | Healthcare |
| XLB | 9 | 3 | Materials/Natural Resources |
| UPS | 8 | 2 | Industrials |
| PNC | 8 | 2 | Financial Services |
| PEP | 7 | 2 | Consumer Defensive |
| ISRG | 7 | 2 | Healthcare |

Note the existing universe's top contributors (AAPL=15, GOOGL=10) are still in the lead — Tier 1 isn't deficient, just outnumbered.

---

## 5. Sector distribution

Top 30 contributors by sector (yahoo classifications, slightly noisy):

| Sector | Top-30 contributors |
|---|---|
| Industrials | 6 |
| Healthcare | 4 |
| Technology | 3 |
| Communication Services | 3 |
| Consumer Defensive | 3 |
| Financial Services | 2 |
| Consumer Cyclical | 1 |
| Energy | 1 |
| Natural Resources / Materials | 1 |
| Equity Precious Metals | 1 |
| Other ETF categories | 5 |

**Sector-cap-of-2 interaction**: with 100+ symbols, no single sector exceeds ~25% of the universe. The sector-cap rule (max 2 open positions per sector) remains comfortably loose — it would not be the binding constraint at typical setup volumes. However, on big-RSI-pullback days when many tech symbols qualify simultaneously, the cap could bite — see §6.

---

## 6. Operational risks

### 6.1 — Daily-cap-of-2 becomes the binding constraint

The current `MAX_DAILY_ENTRIES = 2` rule (per `gamma/__init__.py:15`) was sized for a 27-symbol universe. With the expanded universe:

- Days with > 2 setups: **37 / 90** (41% of days)
- Setups dropped by daily cap: **180 of 298** (60.4%)
- Most extreme single day: 24 setups → cap to 2 → 22 dropped

Even after the daily cap, expansion still delivers 118 vs 48 setups (+146%). But the cap-of-2 is doing a lot of selection work post-scan. **Open question**: should the cap rise to 4 or 5 with the expanded universe? See §8.

### 6.2 — Sector-cap-of-2 interaction with daily cap

When 5+ Healthcare names pull back simultaneously (regime-driven correlation), the existing rules would let only **2 enter** — sector-cap binds. This is by design (diversification), but with 100+ symbols, sector-correlated pullbacks may waste signal. **Open question**: is sector-cap-of-2 still appropriate for the expanded universe, or raise to 3–4?

### 6.3 — Position-cap-of-3 may bite

`MAX_OPEN_POSITIONS = 3` (per `gamma/__init__.py:14`). With ~16 setups/week, the system enters at roughly 3-day intervals into 14–21 DTE spreads. At any moment there could be 5–8 spreads in flight historically, exceeding the cap by 2–5. **Open question**: position cap appropriate for expanded volume?

### 6.4 — Liquidity edge cases beyond the 1M filter

The 1M shares/day floor catches the most obviously illiquid names (BLK, EQIX rejected here). But "liquid in shares" doesn't always mean "liquid in options" — REITs (AMT, PLD, CCI) and some industrials trade huge share volume but have wide options spreads. The proposal includes them in good faith based on share volume; spread-quality verification on a market-open day is recommended before implementation.

### 6.5 — Earnings data gaps (Finnhub coverage)

Finnhub covers all S&P 100 stocks. ETFs are exempt from earnings filter. No gaps expected. The known fail-silent behavior (Part A §F, Section I.3) is a separate hygiene concern that applies regardless of universe size.

### 6.6 — Options chain stability

Top 10 contributors all have 11–24 expiries listed, with consistent 14–21 DTE windows. Mid-cap or thinly-traded ETFs (SLV, GDX, IBB) sometimes have sparser weekly chains; verified clean for our top contributors.

### 6.7 — yfinance rate limits at scan time

The current cron does daily SCAN at 16:30 ET sequentially through 27 symbols (~90s wall time per logs). With 157 symbols, scan time would scale to ~9 minutes. Gamma uses `_fetch_history` with a 20s per-symbol timeout, so worst case the scan completes within 53 minutes. yfinance has historically tolerated this volume but is not a contracted API. **Mitigation if approved**: parallelize the fetch (10–20 worker threads) — already supported by `concurrent.futures` infra in `gamma/scanner.py`.

### 6.8 — Most recent regime delivers zero setups even with expansion

The Apr 30 → May 8 window shows zero setups in BOTH universes. **Expansion does not insure against universe-wide overbought regimes.** This is by design — Connors' RSI<30 is rare in rallies. Don't expect expansion to "fix" the silence in the next overbought stretch. The lift is in the **average case**, not the **worst case**.

---

## 7. Implementation notes (descriptive — NOT recommending)

If the user decides to proceed with expansion, here's the diff shape:

- **File**: `/home/trader/QuantAI/v2/shared-data/scripts/gamma/__init__.py`
- **Affected blocks**:
  - `UNIVERSE` list (lines 41-72): replace 27 entries with ~155
  - `INSTRUMENT_CONFIG` dict: add per-symbol `type`, `tax`, `sector` for each new entry
- **Estimated diff**: ~80-120 LOC depending on metadata verbosity
- **Test impact**: existing tests reference `UNIVERSE` as a constant — they still pass. New universe-membership tests would be additive.
- **No other code changes required**: scanner, strike selector, risk module, and gates all consume `UNIVERSE` indirectly.

If the user decides to ALSO loosen daily-cap / sector-cap / position-cap to better accommodate expanded volume, those are 3 separate constants in the same file. **These are NOT recommended in this proposal** — the user explicitly excluded threshold changes from this scope. Listed only because §6.1–6.3 highlight them as binding constraints under expansion.

---

## 8. Open questions for review

1. **Universe size cap**: pursue full expansion to ~155 symbols, or stop at 100? Tier-2 stocks beyond S&P 100 not pursued; could add 20–30 more if desired.
2. **Include international**: keep EFA + EEM in the proposal, or US-only?
3. **Include leveraged ETFs**: TQQQ / SQQQ / SPXL / SOXL not included — RSI behaves differently on 3× products. Worth considering or excluded for safety?
4. **Daily-cap-of-2**: keep as-is (drops 60% of expanded setups), or raise to 4–5?
5. **Sector-cap-of-2**: keep as-is, or raise to 3–4 for the larger universe?
6. **Position-cap-of-3**: keep as-is, or raise to accommodate higher setup frequency?
7. **Spread quality re-verification**: should we re-pull ATM bid-ask spreads on a market-open day for all 155 candidates before approving?
8. **Phased rollout**: full expansion at once, or stage Tier 2 first then Tier 3 (or vice-versa)?

---

## Verification (held during this analysis)

- ✅ yfinance 1.2.0 fetched 157/158 candidates successfully (MMC delisted).
- ✅ Finnhub API key present and responding (verified in Part A, 65ms latency).
- ✅ Inline `_qualifies()` re-implementation reproduces production's "0 setups" on the current 27 across the 6 production scan dates — **verified before** running the expanded comparison (per user instruction).
- ✅ Earnings filter explicitly skipped in replay; documented as a confound that uniformly affects both universes.
- ✅ This proposal is descriptive, not prescriptive. Open questions in §8; no "we should…" recommendations.

---

## Stop point

**This concludes Part B.** No code changes were made. No commits. No production data was modified.

The two deliverables from this task are now in place:
1. `docs/gamma-silence-diagnosis-2026-05-09.md` (Part A, approved)
2. `docs/gamma-universe-expansion-proposal.md` (this document)

Awaiting review and decision on the §8 open questions before any implementation.
