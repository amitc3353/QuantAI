# Skill: RSI Pullback Mechanics (Connors Method)

**Used by:** Gamma (primary), Alpha (reference)
**Loaded when:** Gamma scan, Gamma entry validation, post-trade analysis

---

## The Core Thesis

When a stock or index in a confirmed long-term uptrend (price > 200-day SMA) becomes short-term oversold (RSI(10) < 30), it snaps back to fair value approximately 89% of the time. This is not a prediction about the stock — it's a statistical property of how mean reversion works in trending assets.

**Why it works:** In an uptrend, short-term pullbacks are caused by temporary selling pressure (profit-taking, sector rotation, broad risk-off). The underlying trend forces reassert themselves because the fundamental conditions that created the uptrend haven't changed. Buyers step in at perceived discounts.

**When it fails:** The 11% failure rate comes from regime changes — when the pullback IS the start of a trend reversal. The 200-day SMA filter catches most of these (price falls below → no longer in uptrend), but it lags. A stock can drop 10% in 3 days while still technically above its 200-day SMA.

---

## RSI(10) — Wilder's Smoothing, Period 10

**CRITICAL:** Use period=10, NOT period=14. Connors' backtest results (88.89% win rate) are specifically calibrated to RSI(10). Using RSI(14) — the default in most charting libraries — produces different signals with different performance characteristics.

**Wilder's smoothing vs Simple MA:** Wilder's method uses exponential-style smoothing: `avg_gain = (prev_avg_gain × (period-1) + current_gain) / period`. This is different from a simple moving average of gains/losses. Most libraries implement Wilder's correctly, but verify.

**Warmup period:** RSI needs at least `period` data points to initialize, plus additional data for the smoothing to stabilize. Use 220+ daily closes minimum (200 for the SMA + 20 buffer for RSI warmup). Fetching 252 trading days (1 year) is safest.

```python
def calculate_rsi(closes, period=10):
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
```

---

## Entry Signal — All Must Be True

1. **Price > 200-day SMA** — confirmed uptrend
2. **RSI(10) < 30** — short-term oversold
3. **Not within 7 trading days of earnings** — stocks only (not index/ETF)
4. **Open Gamma positions < 3** — concentration limit
5. **No more than 2 positions in same sector** — diversification
6. **Circuit breaker not active** — 3 consecutive losses → 48h pause

**Why RSI < 30 and not RSI < 20 or RSI < 25:**
- RSI < 20 is too restrictive — misses 60% of profitable setups
- RSI < 25 is slightly better on win rate but reduces signal frequency significantly
- RSI < 30 is Connors' calibrated threshold — optimal balance of signal frequency and win rate
- Deeper oversold (RSI < 20) does NOT produce better outcomes — the 200 SMA filter already ensures quality

---

## Exit Signal — First Match Wins

1. **RSI(10) > 40** — Mean reversion confirmed. The oversold condition resolved. Take profit.
2. **10 trading days elapsed** — Time stop. If RSI hasn't recovered in 10 days, the pullback is structural, not temporary.
3. **Price closes below 200-day SMA** — Trend broken. The uptrend assumption that justified the entry is gone.
4. **Spread gains ≥ 150%** — Windfall. The underlying moved so much the spread is deep ITM. Take the gift.
5. **Spread loses ≥ 50%** — Early stop. Cut losses before max loss.

**Why RSI > 40 and not RSI > 50:**
- RSI > 50 = overbought territory. Waiting for RSI > 50 means holding through the recovery AND into the next leg up. This works sometimes but increases holding time and drawdown risk.
- RSI > 40 = the oversold condition has normalized. The "snap back" has occurred. Connors' research shows RSI > 40 captures 80%+ of the available mean-reversion move.

---

## Pullback Behavior by Instrument Type

### Index/ETF (SPX, QQQ, SPY, IWM)
- Pullbacks are less frequent (broad diversification dampens RSI swings)
- Recovery is more reliable (mean reversion is strongest in diversified baskets)
- Expected: 5-8 signals per year, ~90% win rate

### Mega-Cap Stocks (AAPL, MSFT, NVDA, etc.)
- Pullbacks more frequent (single-stock events, earnings, guidance)
- Recovery slightly less reliable (single-stock risk is higher)
- Expected: 1-3 signals per stock per year, ~85% win rate
- **Semiconductor stocks (NVDA, AMD, AVGO)** tend to have deeper, faster pullbacks and faster recoveries. The signal works well but entry timing matters more.

### Defensive Stocks (PG, JNJ, COST)
- Pullbacks are rare (low beta, stable business)
- When they happen, recovery is very reliable
- Expected: 0-1 signals per stock per year, ~92% win rate
- Premium is thinner (low IV), so debit spreads may not be worth the capital allocation

---

## Known Failure Modes

1. **Sector-wide rotation:** When an entire sector rotates out of favor (not a single-stock event), multiple stocks trigger RSI < 30 simultaneously. The individual pullbacks aren't independent — they're correlated. Sector diversification limits mitigate this, but a broad tech selloff can still hit 3+ positions.

2. **Trend break during holding:** Stock gaps below 200 SMA overnight. By the time the trend break exit triggers, the loss is larger than expected because intraday monitoring can't catch overnight gaps.

3. **False signal near earnings:** RSI drops below 30 just before earnings (anticipatory selling). The pullback isn't mean-reverting — it's pricing in expected bad news. The 7-day blackout catches most of these, but a stock can start selling off 10 days before earnings.

4. **Broad market crash:** 2020 COVID, 2022 rate hikes — everything drops below 200 SMA simultaneously. Gamma correctly stops trading (200 SMA filter blocks entries), but positions already open take full losses. This is the 11% failure case.

---

## Self-Diagnosis Questions for Gamma

- Was the RSI reading accurate? (check data freshness, warmup period)
- Did the stock have an unusual catalyst (not in my data) that explains the pullback?
- Was the pullback sector-correlated or stock-specific?
- Would a different RSI threshold (< 25 or < 35) have improved this trade?
- Was the 200-day SMA calculation correct? (check data quality, adjusted vs unadjusted close)
- Did the debit spread capture the underlying move efficiently? (vega drag, slippage)
