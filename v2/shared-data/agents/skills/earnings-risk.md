# Skill: Earnings Risk

**Used by:** Alpha, Beta, Gamma
**Loaded when:** Scanner stage, entry validation, weekly universe refresh

---

## Why Earnings Are a Hard Blackout

Earnings are binary events. The stock either beats, meets, or misses — and the resulting move is largely unpredictable. A stock with perfect technicals can drop 15% on an earnings miss. A stock with terrible RSI can gap up 20% on a surprise beat.

**Premium selling around earnings is not "selling rich volatility" — it's selling binary lottery tickets.** The premium IS rich because the risk IS real. The house edge that exists in normal premium selling (theta + mean reversion) does not exist through earnings.

---

## Blackout Rules by Agent

| Agent | Blackout Period | Universe Affected |
|---|---|---|
| Alpha | 14 days before earnings | All 78 tickers scanned |
| Beta | N/A | Trades SPX/XSP/VIX only — no earnings |
| Gamma | 7 days before earnings | 20 individual stocks only (not index/ETF) |

**Alpha's 14-day buffer is conservative.** Tastytrade research suggests 7 days is sufficient for most stocks, but Alpha trades higher IV Rank tickers where pre-earnings IV ramp starts earlier. 14 days avoids entering a position that will be IV-ramped at entry and IV-crushed at exit.

**Gamma's 7-day buffer is tighter** because Gamma's holding period is short (avg 4-5 days, max 10). A 14-day buffer would eliminate many valid signals. The 7-day buffer ensures Gamma's spread won't be open through earnings.

---

## Post-Earnings Behavior

### First 48 Hours After Earnings
- IV crushes 30-60% immediately
- Stock re-prices on new information
- Bid-ask spreads may widen temporarily
- Gap fills or gap continuation are both common — no reliable pattern

**Rule:** Neither Alpha nor Gamma should enter within 2 trading days AFTER earnings. The post-earnings price action is noisy, and IV is too depressed for premium selling (Alpha) and the pullback might be earnings-driven rather than technical (Gamma).

### Earnings Surprise Asymmetry (Mega-Caps)
- **Beat:** Stocks often gap up modestly, then drift. IV crushes hard.
- **Miss:** Stocks often gap down violently, overshoot, then partially recover. IV stays elevated longer.
- **Implication for Gamma:** A post-earnings RSI < 30 is often a valid signal IF it occurs 3+ days after earnings and the stock is above 200 SMA. The oversold condition is real (not pre-earnings fear). But within 2 days, it's earnings noise.

---

## Data Source Requirements

### Earnings Calendar
- **Primary:** Finnhub API (currently used)
- **Frequency:** Refresh daily (after market close)
- **Coverage:** All 27 Gamma instruments + Alpha's 78-ticker universe

### Known Issues
1. **Unscheduled pre-announcements.** Companies occasionally pre-announce results before the scheduled date. No calendar API captures these reliably. This is an unavoidable gap.
2. **Calendar API lag.** Some APIs report the PREVIOUS quarter's earnings date until the new date is announced. Always check that the reported date is in the future.
3. **Extended hours earnings.** Most mega-caps report after close (4 PM) or before open (8 AM). The blackout counts calendar days, not trading days, to cover both cases.

---

## Pull the Full Ticker Universe for Earnings

**Architecture principle (from Amit's learnings):** Pull earnings data for the entire ticker universe, not just the current watchlist. Partial data creates blind spots.

```python
# WRONG — only checks tickers that passed the scanner
for ticker in scanned_tickers:
    check_earnings(ticker)

# RIGHT — checks ALL tickers in the universe
for ticker in FULL_UNIVERSE:  # 78 for Alpha, 27 for Gamma
    earnings_data[ticker] = fetch_earnings_date(ticker)
```

If a ticker is excluded from scanning because its earnings date is missing or stale, that's a data coverage gap — log it for self-diagnosis.

---

## Self-Diagnosis Questions for Earnings

- Was the earnings calendar accurate for this ticker? (verify against actual earnings date)
- Did a pre-announcement occur that my data didn't capture?
- Did I enter a position that was within the blackout window due to stale calendar data?
- Was the post-earnings RSI signal (Gamma) driven by earnings reaction or by a technical pullback?
