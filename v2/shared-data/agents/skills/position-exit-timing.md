# Skill: Position Exit Timing

**Used by:** Alpha, Beta, Gamma (via position_monitor.py)
**Loaded when:** Every 2-min position monitor cycle, post-trade analysis

---

## The Exit Is More Important Than the Entry

A good entry with a bad exit = losing trade. A mediocre entry with a disciplined exit = surviving trade. Exit rules must be mechanical, pre-defined at entry, and not overridden by hope or fear.

---

## Agent-Specific Exit Rules

### Alpha — Credit Spreads / Diagonals / Condors
| Condition | Action | Rationale |
|---|---|---|
| P&L ≥ 50% of max credit | Close — take profit | Tastytrade research: taking profit at 50% maximizes risk-adjusted returns. Going for 100% doubles holding time with marginal gain. |
| P&L ≤ -200% of credit | Close — stop loss | A 2× loss relative to credit collected means the position is moving against you decisively. Don't wait for max loss. |
| 3:30 PM ET | Close all | No overnight risk management possible. Flat before close. |
| ≤ 3 DTE | Close | Gamma risk accelerates. Pin risk becomes real. |

### Beta — Strategy-Dependent
| Strategy | Take Profit | Stop Loss | Time Exit | Special |
|---|---|---|---|---|
| Debit spreads | 60% of max profit | 1× risk (100% loss) | — | Trend reversal (price crosses 20 SMA) |
| Iron condors | 50% of credit | 2× credit | 2 DTE | — |
| Event strangles | Immediately after event move | 100% of premium | Morning after event | Close regardless of P&L post-event |
| VIX mean reversion | 50% profit | VIX re-spikes (backwardation returns) | — | — |
| Long straddles | 100% profit | 50% loss | 7 DTE | — |

### Gamma — RSI Pullback
| Condition | Action | Rationale |
|---|---|---|
| RSI(10) > 40 | Close — RSI recovery | Thesis confirmed. The pullback bounced. |
| 10 trading days elapsed | Close — time stop | Thesis failed. Pullback didn't recover in expected timeframe. |
| Price closes below 200-day SMA | Close — trend break | The uptrend assumption is invalid. |
| Spread gains ≥ 150% | Close — windfall | Take the gift. Don't wait for RSI signal. |
| Spread loses ≥ 50% | Close — stop loss | Cut early. Preserve capital. |

---

## The 50% Rule for Premium Sellers (Tastytrade Research)

Research from tastytrade across thousands of trades shows:
- Closing credit spreads at **50% of max profit** produces the highest risk-adjusted returns
- It reduces average holding time by ~40%
- It frees up capital for new trades sooner
- The last 50% of profit takes disproportionately more time and carries higher risk of reversal

**This is why Alpha's target is 50%, not 80% or 100%.** The math favors smaller, faster wins.

---

## Time Decay Curve — When Theta Accelerates

```
Days to Expiry:  30   25   20   15   10    7    5    3    1
Theta Factor:    1.0  1.1  1.3  1.5  1.8  2.2  2.8  3.5  5.0+
```

**Practical implication:** An option losing $0.05/day at 30 DTE might lose $0.25/day at 5 DTE. For sellers, this acceleration is the profit engine. For buyers (Gamma), this acceleration is the enemy — exit before the curve steepens.

---

## Common Exit Mistakes

1. **Moving the stop loss.** "It's close to my stop but I think it'll recover." → Loss doubles. Never move stops. They were set with a clear head at entry for a reason.

2. **Not taking profit at target.** "It's up 45%, close to my 50% target, let me wait for 60%." → Market reverses, gives back all gains. Take the target when it's there.

3. **Averaging down on losers.** Adding to a losing position is not an exit strategy. It's doubling the mistake.

4. **Ignoring time stops.** Gamma's 10-day time stop exists because a pullback that hasn't recovered in 10 days is fundamentally different from one that recovers in 3. The thesis is wrong. Accept it.

5. **Holding through unexpected events.** An unscheduled FOMC announcement, a geopolitical shock, a company scandal — these invalidate whatever thesis existed. Close and re-evaluate.

---

## Position Monitor Performance Metrics

Track these in weekly synthesis:
- **Average holding time** (by agent, by strategy) — shorter is generally better for premium sellers
- **Profit target hit rate** — what % of trades reach the target before the stop?
- **Time stop frequency** (Gamma) — should be < 15% based on backtest
- **Stop loss frequency** — if > 30%, the entry criteria might be too loose
- **3:30 PM forced close rate** (Alpha) — should be rare; frequent forced closes suggest entries are happening too late

---

## Self-Diagnosis Questions for Exits

- Did the exit trigger at the right time, or was there a lag?
- Would a different profit target (40% vs 50% vs 60%) have improved outcome?
- Was the stop loss too tight (stopped out before recovery) or too loose (took unnecessary damage)?
- Did the position monitor's 2-minute cycle cost money on this exit?
- Was there a signal I didn't have that would have triggered an earlier/later exit?
