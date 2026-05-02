# Skill: Regime Classification

**Used by:** Alpha (market intelligence), Beta (12-regime classifier)
**Loaded when:** Every 15-min cycle, regime gate checks

---

## Why Regime Matters

The same strategy behaves differently in different market environments. A credit spread that wins 70% of the time in NORMAL regime might lose 70% in CRISIS. Regime classification is not a filter — it's the foundation of every trade decision.

**Alpha's approach:** 4-level regime (halt / risk_off / caution / normal) synthesized from warning flags. Simpler, drives gate decisions (trade/don't trade/reduce size).

**Beta's approach:** 12-level regime with strategy assignment per regime. More granular, drives strategy selection (which trade to take).

Both are valid for their purpose. Alpha doesn't need 12 regimes because its debate chamber provides the nuance. Beta needs 12 because it's deterministic — the regime IS the decision.

---

## VIX as the Primary Regime Input

| VIX Range | Market State | Historical Context |
|---|---|---|
| < 13 | Unusually calm | Complacency. Premium is cheap. Often precedes a spike. |
| 13–17 | Normal / calm | Business as usual. Moderate premium. |
| 18–23 | Elevated | Mild stress or event anticipation. Premium is getting rich. |
| 24–29 | High | Significant stress. Hedging demand rising. Wide bid-asks. |
| 30–34 | Danger | Institutional panic hedging. Backwardation likely. |
| ≥ 35 | Crisis | Market in free-fall or systemic event. No trading. |

**Important:** VIX is a 30-day forward-looking measure derived from SPX option prices. It doesn't tell you what happened — it tells you what the market EXPECTS to happen. A VIX of 25 doesn't mean the market fell. It means the market expects ~1.6% daily moves over the next 30 days.

---

## VIX Term Structure — The Leading Indicator

The VIX spot vs VIX3M (3-month VIX) relationship is often more informative than VIX level alone.

**Contango (VIX < VIX3M):** Normal state. Market expects current conditions to persist or improve. ~80% of the time, the term structure is in contango.

**Backwardation (VIX > VIX3M):** Stress signal. Market expects MORE volatility now than in 3 months. Institutional hedging is concentrated in near-term options. This is the strongest single signal for Beta's CRISIS detection.

**Flattening (VIX ≈ VIX3M):** Transition state. Watch closely — often precedes a move to backwardation.

---

## Beta's 12-Regime Decision Tree

Priority order (first match wins):

```
1. VIX ≥ 35                                    → HALT
2. VIX ≥ 30 + backwardation + SPX < 200 SMA    → CRISIS
3. Major event within 24h                       → PRE_EVENT
4. VIX spike > 5 pts in 1 day + contango returning → MEAN_REVERSION
5. VIX 25–35 + contango                         → HIGH_VOL
6. BB width < 2% + ADX < 20                     → SQUEEZE
7. SPX > 20/50/200 SMA + ADX > 25               → TREND_UP
8. SPX < 20/50/200 SMA + ADX > 25               → TREND_DOWN
9. VIX < 13                                     → LOW_VOL
10. RSI 35–65 + ADX < 25 + VIX 13–25            → RANGE
11. Everything else                              → NORMAL
```

---

## Alpha's 4-Level Regime

```
HALT      → VIX ≥ 35 OR any HALT flag
RISK_OFF  → 2+ WARNING flags OR VIX ≥ 28
CAUTION   → Any WARNING flag OR VIX ≥ 22
NORMAL    → Everything else
```

Warning flags come from: Fear & Greed score, yield curve inversion, economic calendar events, VIX term structure.

---

## Regime Transitions — What to Watch

| Transition | Signal | Action |
|---|---|---|
| NORMAL → CAUTION | VIX crosses 22, or first warning flag | Alpha: reduce size. Beta: check if RANGE still valid. |
| CAUTION → RISK_OFF | Second warning flag, or VIX crosses 28 | Alpha: no new entries. Beta: close range positions. |
| RISK_OFF → HALT | VIX crosses 35 | Everything stops. Monitor only. |
| HALT → RISK_OFF | VIX drops below 35 | Watch for VIX mean reversion setup (Beta). |
| Any → PRE_EVENT | Major event enters 24h window | Beta: shift to event strangles. Alpha: no new entries. |

---

## Regime Classification Errors — What Goes Wrong

1. **Stale VIX data.** If VIX cache is 90 minutes old and VIX moved 3 points, you're in the wrong regime. This is the #1 capability gap to watch for.

2. **Conflicting signals.** VIX = 19 (normal) but yield curve inverted and Fear & Greed = 15. Alpha's flag system handles this. Beta should weight VIX term structure over spot VIX in ambiguous cases.

3. **Regime flicker.** VIX bouncing between 21.8 and 22.3 causes NORMAL → CAUTION → NORMAL flip-flopping. **Hysteresis is needed:** once CAUTION triggers, require VIX < 20 (not 22) to return to NORMAL. Prevents whipsaw.

4. **Event calendar miss.** If the economic calendar data source doesn't capture a surprise event (emergency FOMC, geopolitical shock), the regime classification is wrong. No fix except better data coverage.

---

## Self-Diagnosis Questions for Regime

After every trade, the self-diagnosis system should ask:
- Was the regime classification correct based on what actually happened?
- Did the regime change during the life of the trade? If so, should I have exited on regime change?
- Was VIX data fresh enough to classify accurately?
- Were there signals I didn't have that would have changed the classification?
