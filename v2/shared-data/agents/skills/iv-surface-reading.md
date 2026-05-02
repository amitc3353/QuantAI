# Skill: IV Surface Reading

**Used by:** Alpha, Beta
**Loaded when:** Every scan cycle, strike selection, entry validation

---

## IV Rank vs IV Percentile — Know the Difference

**IV Rank** = (Current IV - 52-week low IV) / (52-week high IV - 52-week low IV) × 100
- Measures where current IV sits relative to its annual range
- IV Rank of 40 means current IV is 40% of the way from its yearly low to yearly high
- **Problem:** One extreme spike (earnings, crash) can skew the range and make normal IV look artificially low

**IV Percentile** = % of days in the last year where IV was LOWER than today
- IV Percentile of 80 means IV was lower than today on 80% of trading days in the past year
- More robust to outlier spikes
- **Better for premium selling decisions** — tells you how often you'd get a better price by waiting

**Rule of thumb:** When IV Rank and IV Percentile diverge significantly (e.g., Rank=30 but Percentile=65), trust Percentile for premium selling decisions. The Rank is being distorted by an outlier.

---

## Term Structure — Contango vs Backwardation

**Contango (normal):** Near-term IV < far-term IV. Market expects current calm to persist. Calendar spreads and diagonals benefit — you're selling expensive near-term, buying cheaper far-term.

**Backwardation (stress):** Near-term IV > far-term IV. Market is pricing immediate danger. Institutional hedging demand is spiking. **Key signal for Beta's regime classifier** — backwardation + VIX > 30 = CRISIS regime.

**Measuring it:** VIX vs VIX3M. If VIX > VIX3M, the term structure is inverted (backwardation). The ratio (VIX3M - VIX) / VIX × 100 gives the contango percentage. Negative = backwardation.

---

## IV Skew — Put vs Call Pricing

**Normal skew:** OTM puts are more expensive than equidistant OTM calls. This is structural — institutional demand for downside protection.

**Why it matters for Alpha:**
- Bull put spreads: you're selling into the rich side of the skew (good — more premium)
- Bear call spreads: you're selling into the cheap side (less premium for the same delta)
- This is why bull put spreads often score higher than bear call spreads at the same IV Rank

**Skew steepening:** When skew gets steeper (puts getting even more expensive), it signals increasing fear. Often precedes a selloff. **Not a reason to avoid bull puts** — the premium is richer precisely because of the fear — but it IS a reason to widen strikes and reduce size.

---

## IV Crush — Post-Event Behavior

After binary events (earnings, FOMC), IV collapses rapidly regardless of direction. This is "IV crush."

**Quantifying it:** Typical earnings IV crush is 30-60% of pre-event IV, depending on the stock's historical pattern. Mega-caps (AAPL, MSFT, GOOGL) crush harder (40-60%) because more hedging activity pre-earnings. Smaller names crush less predictably.

**For Alpha:** Earnings blackout (14 days) means Alpha never holds through crush. This is correct — the crush itself isn't the risk. The risk is the directional move that comes with it.

**For Beta:** PRE_EVENT strangles are designed to profit from the move that causes the crush. Buy vol before, sell after. The strangle profits if the move exceeds the vol premium paid.

---

## Practical Filters

| Condition | Interpretation | Action |
|---|---|---|
| IV Rank < 20 | Premium is cheap | Alpha: skip (not enough edge). Beta: skip RANGE condors. |
| IV Rank 20–35 | Below average | Alpha: credit spreads only (no diagonals). Beta: normal. |
| IV Rank 35–55 | Average to rich | Alpha: full green light, diagonals preferred. Beta: normal. |
| IV Rank 55–75 | Rich | Alpha: excellent setup. Widen strikes slightly for margin of safety. |
| IV Rank > 75 | Extremely rich | Alpha: be cautious — this might be rich for a reason (event, stress). Check WHY before selling. |
| IV Percentile > IV Rank + 20 | Skewed by outlier | Trust percentile. Premium is richer than Rank suggests. |
| Term structure inverted | Near-term stress | Avoid calendars/diagonals (near-leg is too expensive). Credit spreads still viable. |
