# Skill: Greeks Management

**Used by:** Alpha, Beta, Gamma
**Loaded when:** Position sizing, strike selection, exit timing, risk assessment

---

## The Greeks That Matter for Premium Selling

### Theta (Time Decay) — Your Paycheck
- Theta tells you how much value the option loses per day, all else equal
- **For sellers (Alpha condors/spreads):** Positive theta = making money every day the underlying stays still
- **For buyers (Gamma debit spreads, Beta strangles):** Negative theta = bleeding money every day. Speed matters — exit fast.
- Theta accelerates in the last 7-14 DTE. This is why Alpha targets 5-21 DTE for credit spreads — maximum theta decay rate
- **Non-linear decay curve:** An option loses ~1/3 of its time value in the first half of its life, and ~2/3 in the second half. Selling at 21 DTE and closing at 10 DTE captures the sweet spot

### Delta — Directional Exposure
- Delta = how much the option price moves per $1 move in the underlying
- **Alpha's short delta target: 0.05–0.20.** This means the short strike is well OTM. A $1 move in the underlying only moves the short option $0.05–$0.20
- **Gamma's long delta target: 0.50–0.55 (ATM buy), 0.25–0.30 (OTM sell).** The spread's net delta is ~0.25, so a $1 up move gains ~$0.25 per contract
- **Beta's delta varies by strategy.** Condors are delta-neutral at entry. Directional spreads target net delta 0.25–0.35

### Gamma — Delta's Rate of Change
- Gamma tells you how fast delta changes as price moves
- **Highest at ATM, near expiration.** This is why short ATM options near expiry are dangerous — small price moves create large delta swings
- **For Alpha:** Gamma risk is managed by staying OTM (short delta 0.05–0.20) and closing before 3 DTE
- **For Beta:** Event strangles WANT gamma. High gamma near ATM means large payoff on big moves
- **Gamma risk red flag:** If any position's short gamma exceeds 0.05 per contract, the position is getting dangerous. Consider closing

### Vega — Volatility Sensitivity
- Vega = how much the option price changes per 1% change in implied volatility
- **For sellers:** Negative vega = you profit when IV drops. This is why selling after IV spikes (high IV Rank) works — you sell expensive, IV drops, you buy back cheap
- **For buyers (Gamma):** Positive vega = you lose when IV drops. This is why Gamma enters during oversold conditions (often elevated IV) and exits on recovery (IV normalizes). The vega loss partially offsets the directional gain — factor this into profit expectations

---

## Position Sizing by Greeks

### Alpha Sizing
```
Max risk per trade: 2% of account ($400 on $20k)
For credit spreads: max_loss = (spread_width - credit) × 100 × contracts
Solve for contracts: contracts = floor($400 / max_loss_per_contract)
```

### Beta Sizing
```
Max risk per trade: 1% of account ($100 on $10k)
For debit spreads: max_loss = debit_paid × 100 × contracts
Solve for contracts: contracts = floor($100 / debit_per_contract)
```

### Gamma Sizing
```
Max risk per trade: 1% of account ($100 on $10k)
For debit spreads: max_loss = debit_paid × 100 × contracts
Solve for contracts: contracts = floor($100 / debit_per_contract)
If floor = 0, skip the trade (can't size it within risk budget)
```

---

## Greeks-Based Exit Signals

| Signal | What It Means | Action |
|---|---|---|
| Short delta > 0.30 on a credit spread | Underlying approaching short strike | Alpha: consider early close. Position getting directional. |
| Theta < $0.01/day on a credit spread | Almost no time value left to decay | Close — you've captured most of the premium |
| Gamma > 0.05 on a short position near expiry | Pin risk is real | Close before assignment risk materializes |
| Vega exposure > account risk limit | A vol spike could blow through stop | Reduce position or add a hedge leg |
| Net delta across all positions > ±0.50 | Portfolio is directionally biased | Don't add more in the same direction |

---

## Common Mistakes

1. **Ignoring gamma near expiration.** A position that's "safe" at 14 DTE can become dangerous at 2 DTE if price drifts near the short strike. Alpha's 3:30 PM hard close and no-holds past 3 DTE mitigate this.

2. **Confusing high theta with high edge.** An option with $0.50/day theta decay might also have $2.00/day gamma risk if the underlying moves. Net expected P&L matters, not theta alone.

3. **Sizing by notional instead of risk.** A 1-lot $5-wide spread on a $500 stock is the same risk as a 1-lot $5-wide spread on a $50 stock. Size by max loss, not by underlying price.

4. **Ignoring vega on debit spreads.** Gamma's bull call spreads have positive vega. If you enter during an IV spike and IV normalizes, your spread loses value even if the stock goes your way. This means Gamma's actual win rate on spreads may be slightly below the 88.89% backtest on shares.
