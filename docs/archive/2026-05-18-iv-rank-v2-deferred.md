# Genuine IV-Rank Work — Deferred v2 Item

**Date:** 2026-05-18
**Status:** Deferred — requires infrastructure not yet built

## Context

During the regime gate + IV-rank improvement analysis for Gamma, we
identified that `_beta_intel.compute_iv_rank_252d()` measures **realized
volatility percentile** (21-day HV rank over 252 days), NOT implied
volatility rank. The function name is misleading.

The original upgrade thesis (#1 from the expert deep-dive) was about
**vega overpayment**: Gamma buys debit spreads without checking if IV is
cheap or expensive. That thesis requires implied vol from the option
chain — the market's forward-looking pricing — not backward-looking
realized vol.

## What shipped (commit 1, 2026-05-18)

- Regime gate in `run_scan_4arm()` using broad-market signals from
  `market_intelligence.json` (VIX level/regime, term structure, SPY
  trend). This is a macro-level filter, honestly named.

## What is deferred

1. **Realized-vol filter (rvol):** Using `compute_iv_rank_252d` honestly
   renamed as `RVOL_BLOCK_THRESHOLD` / `RVOL_HALF_THRESHOLD`. Useful as
   a "has this stock been choppy recently?" filter. No strike geometry
   change. Planned as commit 2 in a follow-up session.

2. **Genuine IV-rank (v2 item):** Requires building a daily IV-history
   store:
   - Fetch per-symbol ATM implied vol from option chains daily
   - Store in a time-series file (e.g. `iv_history/{symbol}.jsonl`)
   - Accumulate 252 trading days of history before the rank is meaningful
   - Compute `(current_iv - 252d_low) / (252d_high - 252d_low) * 100`
   - THEN: IV-rank gating (block IVR>80, half IVR>60) and IV-aware
     strike geometry (wider deltas when IV is rich) are theoretically
     justified

   **Estimated build:** ~200 LOC collector + ~50 LOC integration into
   scanner + 252 days of data accumulation before the rank is usable.

3. **IV-aware strike geometry:** Deferred entirely. The delta adjustment
   (0.55/0.20 when IV is rich vs 0.50/0.27 when cheap) was justified
   by implied-premium richness. A strike change triggered by realized
   vol has no theoretical basis. This waits for genuine IV-rank (item 2).

## Key lesson

`compute_iv_rank_252d` is a realized-vol-rank function. It should be
renamed to `compute_rvol_rank_252d` when next touched. The "IV" in the
name is a misnomer inherited from Beta's regime detector where the
distinction was less critical (regime classification, not order-path
gating).
