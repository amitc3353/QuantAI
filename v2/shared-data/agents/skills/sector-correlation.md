# Skill: Sector Correlation

**Used by:** Alpha, Gamma (Beta trades index-only, less relevant)
**Loaded when:** Pre-entry position check, weekly synthesis

---

## The Problem

If Alpha opens a bull put spread on NVDA and Gamma opens a bull call spread on AMD, both are long tech. A tech selloff hits both simultaneously. The trades looked independent at entry but their outcomes are correlated.

**Concentration risk is invisible at the single-trade level.** It only appears when you look across all open positions system-wide.

---

## Sector Mapping

| Sector | Tickers in Universe |
|---|---|
| Technology | AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AVGO |
| Financials | JPM, V, MA, BRK.B |
| Healthcare | UNH, JNJ, LLY |
| Consumer | HD, PG, COST |
| Energy | XOM, CVX |
| Semiconductor sub-sector | NVDA, AMD, AVGO, TSM, MU, ASML |
| Index/ETF | SPY, QQQ, IWM, SPX, XSP, RUT, NDX |

**Note:** AMZN and TSLA are classified as Technology here (consumer-tech hybrid), not Consumer. Their price behavior correlates with tech, not retail.

---

## Correlation Rules

### Hard Rules (enforced at execution layer)
1. **Gamma:** Max 2 positions in same sector at any time
2. **System-wide:** Total open positions across all agents ≤ 3 (currently shared cap)

### Soft Rules (flagged in self-diagnosis, not enforced)
3. If 2+ positions are in the same semiconductor sub-sector, flag as high correlation
4. If all open positions are in Technology, flag as sector concentration
5. Index positions (SPY, QQQ, SPX) correlate with everything — count them as partial exposure to their heaviest sector (tech for QQQ, broad for SPY)

---

## Cross-Agent Correlation Check

Before any agent enters a trade, the execution layer should:

```python
def check_sector_correlation(new_trade, open_positions):
    """
    Check if new trade creates sector concentration.
    open_positions: all OPEN entries from trades.jsonl (any agent)
    """
    new_sector = get_sector(new_trade["symbol"])
    same_sector_count = sum(
        1 for pos in open_positions
        if get_sector(pos["symbol"]) == new_sector
    )

    # Hard block
    if same_sector_count >= 2:
        return {"blocked": True, "reason": f"Already {same_sector_count} positions in {new_sector}"}

    # Soft warning
    warnings = []
    if same_sector_count == 1:
        warnings.append(f"Adding 2nd position in {new_sector}")

    # Check sub-sector (semis)
    if new_sector == "Technology":
        semi_tickers = {"NVDA", "AMD", "AVGO", "TSM", "MU", "ASML"}
        if new_trade["symbol"] in semi_tickers:
            semi_count = sum(
                1 for pos in open_positions
                if pos["symbol"] in semi_tickers
            )
            if semi_count >= 1:
                warnings.append(f"Semiconductor sub-sector concentration ({semi_count + 1} positions)")

    return {"blocked": False, "warnings": warnings}
```

---

## What Sector Correlation Looks Like in Practice

**Scenario:** Monday, Alpha opens a bull put spread on NVDA. Tuesday, Alpha opens a bear call spread on MSFT (bearish tech). Wednesday, Gamma triggers on AAPL pullback (bullish tech).

**Net exposure:** 2 bullish tech + 1 bearish tech. Partially offsetting, but all three are correlated to the tech sector. A broad tech selloff hits the NVDA bull put (loss) and AAPL bull call (loss), while the MSFT bear call (win) partially offsets.

**The self-diagnosis should catch:** "3 of 3 positions were in Technology. Despite mixed directions, net exposure to sector risk was high. Consider: skip AAPL when NVDA is already open, or add a non-tech position first."

---

## Index-to-Sector Correlation

QQQ/NDX ≈ 50-60% Technology exposure
SPY/SPX ≈ 30% Technology, relatively diversified
IWM/RUT ≈ Low tech concentration, more financials/industrials

**Practical rule:** A QQQ position + an NVDA position = concentrated tech. A SPY position + an NVDA position = moderate concentration. An IWM position + an NVDA position = low concentration.

---

## Self-Diagnosis Questions for Correlation

- How many open positions share the same sector?
- Did correlated positions move in the same direction during this trade's life?
- Would diversifying across sectors have reduced the weekly drawdown?
- Are index positions being double-counted with stock positions in the same sector?
