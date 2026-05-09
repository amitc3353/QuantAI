# QuantAI Roadmap

## Status

**Last updated:** 2026-05-09

Shipped tonight: Phase 2 Item #1 (reflection memory + multi-symbol retrieval + reconciler cron) and Item #2 (JSON parse hardening + retry envelope). All Phase 1 operational gates shipped earlier today.

## Current Phase

**Phase 2 — Memory & Robustness** (weeks 4-5 per original plan)

Remaining items:
- [ ] #11 — Operator feedback memory (Friday digest → Discord replies → `operator_feedback.jsonl` → judge injection)
- [ ] #16 — Skills loader (`_skills_loader.py` + `skills/` directory with 8 Gamma skill files + Alpha/Beta equivalents)

## Phase Queue

**Phase 3 — EV Scoring + Adaptive Learning** (weeks 6-7)
- #3 — OptionLab EV/POP gate (pre-trade expected value + probability of profit)
- #4 + #6 merged — Composite trust score per (strategy, regime) replacing Thompson + circuit breaker

**Phase 4 — Portfolio Intelligence + Calibration** (weeks 8-10)
- #5 — Cross-agent portfolio Greeks aggregation
- #7 — Vector store for similar-setup retrieval (chromadb)
- #10 — Regime detector calibration from outcomes
- #14 — Strategy graceful re-classify on exhaustion
- #17 — Gamma overnight-gap drop logging

**Phase 5 — Backtest Infrastructure** (weeks 11-12)
- #8 — Backtest harness (Optopsy + Polygon options data)
- Gamma Connors revalidation on 27-symbol universe with 2026 data

## Backlog

#13, #15, #18, #19, #20, #21, #27, #28

## How to update

After each shipped item, update Status + remove from Current Phase. After a phase completes, promote next phase to Current.
