# QuantAI Roadmap

## Status

**Last updated:** 2026-05-11

Shipped 2026-05-11:
- **Gamma universe expansion** (PR #4) — 27 → 155 symbols, scanner F0 filter + parallelism. Live since 2026-05-09.
- **Gamma 4-arm A/B/C/D ranker experiment** — 5 commits (`85f9fcb` rankers, `4e6d0da` arm state, `51c3f26` orchestration, `1b9def3` reporting, `d8dbd8c` flag flip + sentinel freeze) + test fix `fd37014`. Flag flipped, Day 0 cron-fires Monday 16:30 ET.

Shipped 2026-05-09: Phase 2 Item #1 (reflection memory + multi-symbol retrieval + reconciler cron) and Item #2 (JSON parse hardening + retry envelope). All Phase 1 operational gates shipped earlier.

## Live experiments

- **Gamma 4-arm A/B/C/D ranker test**
  - Day 0: **2026-05-11**
  - First promotion eval: **2026-07-10** (day 60)
  - Hard cap: **2026-11-07** (day 180; if still inconclusive, ship Arm A by default)
  - Capital: $10K/arm × 4 arms = **$40K** virtual, single IBKR paper account DUP851506
  - Arms: A=RSI_ONLY (control), B=COMPOSITE, C=WEIGHTED_BLEND, D=REWARD_RISK_FIRST
  - Pre-committed promotion rules in `gamma/promotion_evaluator.py` (sample_floor → win_margin+Sharpe → near_tie (Ockham A>D>B>C) → inconclusive_band → hard_cap)
  - Frozen during test: universe, sectors, caps, ranker logic, thresholds (sentinel `NEVER_MODIFY_PATHS`)
  - Friday weekly digest: `30 20 * * 5` cron → Discord
  - Operator commands: `gamma_agent.py --evaluate-promotion | --promote-arm <a|b|c|d> --confirm | --reset-experiment --reason "..." --confirm`
  - Emergency stop: `GAMMA_AB_TEST_ENABLED=0` in `.env`

## Current Phase

**Phase 2 — Memory & Robustness** (weeks 4-5 per original plan)

Remaining items (recommended order):
- [ ] **#16 — Skills loader first** (`_skills_loader.py` + `skills/` directory with 8 Gamma skill files + Alpha/Beta equivalents)
- [ ] **#11 — Operator feedback memory** (Friday digest → Discord replies → `operator_feedback.jsonl` → judge injection)

Rationale for #16 → #11 order: the skills loader is foundational infrastructure that #11's digest-reply handler can lean on for prompt assembly. Doing #16 first lets #11 reuse the loader rather than duplicating prompt-injection plumbing.

Next session focus: Phase 2 closeout (#16 then #11) while Gamma experiment runs autonomously in the background. Watch dashboard https://quantai.tail1465ff.ts.net/ for the experiment banner + weekly digest.

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
- Gamma Connors revalidation on 155-symbol universe with 2026 data (post-experiment)

## Backlog

#13, #15, #18, #19, #20, #21, #27, #28

## How to update

After each shipped item, update Status + remove from Current Phase. After a phase completes, promote next phase to Current. Update Live experiments section when promotion/reset/hard-cap fires.
