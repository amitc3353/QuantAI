---
type: "query"
date: "2026-04-25T03:23:55.372074+00:00"
question: "what calls run_guard_pipeline"
contributor: "graphify"
source_nodes: ["run_guard_pipeline", "check_trade", "check_guards", "autonomous_execution"]
---

# Q: what calls run_guard_pipeline

## Answer

run_guard_pipeline() (guards.py:L364, community 0 — Guard Engine) is called by: check_trade() at guards.py:L452 (the public entry point, EXTRACTED edge). check_trade() is in turn called from autonomous_execution.py via a local check_guards() wrapper (community 9 — Trade Execution); count_open_agent_trades() calls check_guards(). Internally, run_guard_pipeline() orchestrates 18 guard checks: check_halted, check_whitelist, check_position_size, check_max_loss, check_max_contracts, check_min_dte, check_liquidity, check_no_trade_zones, check_earnings_blackout, check_vix, check_cooldown, check_portfolio_delta, check_portfolio_theta, check_max_positions, check_sector_concentration, check_daily_loss, check_bull_put, check_pmcc, check_iron_condor, check_covered_call. Returns APPROVE only if every guard passes.

## Source Nodes

- run_guard_pipeline
- check_trade
- check_guards
- autonomous_execution