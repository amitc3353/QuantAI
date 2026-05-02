# Skill: Execution Quality

**Used by:** Alpha, Beta, Gamma
**Loaded when:** Order submission, fill analysis, post-trade review

---

## mleg Combo Orders — Why They're Non-Negotiable

Every multi-leg options trade MUST be submitted as a single mleg (combo/multi-leg) order. Never leg in.

**Why:**
1. **Margin treatment.** Broker sees defined-risk when all legs are submitted together. Individual legs may be treated as naked/uncovered and rejected.
2. **Fill guarantee.** Either all legs fill or none fill. No "I sold the short leg but the long leg didn't fill" nightmare.
3. **Price guarantee.** You define the net credit/debit. The broker executes at that net price or better. Legging in means you're exposed to price movement between fills.

**Exception:** None. Even if you think you can get a better fill by legging in, don't. The risk of partial fills outweighs any fill improvement.

---

## Strike Snapping

The debate chamber (Alpha) or strategy logic (Beta/Gamma) proposes theoretical strikes. The execution layer must snap these to actually available strikes from the live broker chain.

**Process:**
1. Query broker for available strikes at the target expiry
2. Find the nearest available strike to the proposed strike
3. If the nearest strike shifts the risk/reward by more than 15%, skip the trade
4. Log the snap: `proposed_strike: 135, actual_strike: 134, snap_distance: $1`

**Watch for:** Some tickers have $1 strikes near ATM but $2.50 or $5 strikes further OTM. A $5 snap distance on a $2 credit is 250% slippage — unacceptable.

---

## Slippage Estimation

**For liquid ETF options (SPY, QQQ):** Expect $0.01–$0.03 per leg slippage vs mid-price. Negligible.

**For individual stock options:** Varies widely. Use this heuristic:
- Bid-ask spread < $0.10 → slippage ~$0.02–$0.05
- Bid-ask spread $0.10–$0.30 → slippage ~$0.05–$0.15
- Bid-ask spread > $0.30 → **high slippage risk.** Consider skipping unless premium is very rich.

**For index options (SPX/XSP):** SPX options are among the most liquid in the world. Expect $0.05–$0.20 slippage on mleg combos, which sounds high in absolute terms but is small relative to SPX's notional value.

**Measuring actual slippage per trade:**
```
slippage = |fill_price - mid_price_at_submission|
```
Track this per agent. If average slippage exceeds 5% of net credit/debit, something is wrong — either timing is bad or liquidity filters are too loose.

---

## Timing Considerations

### Avoid the First 15 Minutes (9:30–9:45 AM)
Market makers are still finding prices. Bid-ask spreads are widest. Options premiums are most mispriced. Every agent respects this blackout.

### Avoid the Last 30 Minutes (3:30–4:00 PM)
Liquidity thins as market makers reduce exposure. Fills may be worse. Alpha and Beta hard-close at 3:30 PM.

### Best Execution Window
10:00 AM – 2:30 PM ET. Spreads are tightest, volume is highest, market makers are fully engaged.

### Gamma's Morning Execution (9:33 AM)
Gamma executes 3 minutes after open — early but past the worst noise. This is a tradeoff: you want to capture the bounce before it runs too far, but you also want price discovery to settle. Monitor slippage at 9:33 vs. theoretical fill at 10:00 — if consistently losing more than $0.10, consider pushing execution to 9:45.

---

## Order Types

| Scenario | Order Type | Why |
|---|---|---|
| Standard entry | Limit order at mid-price | Start with mid, don't chase |
| No fill after 60 seconds | Adjust limit by $0.01–$0.02 toward natural side | Small concession, still controlled |
| No fill after 3 minutes | Cancel and reassess | Don't chase. The setup may have moved. |
| Exit at profit target | Limit order at target price | Patient exit, capture full target |
| Exit at stop loss | Limit order, aggressive | Get out. Don't wait for better price on a losing trade. |
| Hard close (3:30 PM) | Market order if necessary | Time > price. Must be flat. |

---

## Post-Execution Logging

Every execution should log:
```json
{
  "proposed_strikes": [135, 130],
  "actual_strikes": [134, 130],
  "snap_distance": [1, 0],
  "proposed_credit": 0.85,
  "fill_price": 0.82,
  "slippage": 0.03,
  "time_to_fill_seconds": 12,
  "order_type": "limit",
  "adjustments_made": 1,
  "fill_timestamp": "2026-05-05T10:31:15-04:00"
}
```

This data feeds the capability self-diagnosis. If slippage is consistently > 5% of credit, it surfaces as a "data freshness" or "execution timing" request.

---

## Self-Diagnosis Questions for Execution

- Was the options chain data fresh at submission time? How old was it?
- Did the fill price differ meaningfully from the proposed price?
- Did the strike snap change the risk/reward profile?
- Would a different execution time have gotten a better fill?
- Was the bid-ask spread at entry within acceptable range?
