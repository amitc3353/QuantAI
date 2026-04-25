# Graph Report - .  (2026-04-25)

## Corpus Check
- 69 files · ~84,283 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 976 nodes · 1727 edges · 51 communities detected
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 247 edges (avg confidence: 0.78)
- Token cost: 21,800 input · 7,700 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Guard Engine Rule Checks|Guard Engine Rule Checks]]
- [[_COMMUNITY_Dependencies and Strategy Config|Dependencies and Strategy Config]]
- [[_COMMUNITY_System Health Checks|System Health Checks]]
- [[_COMMUNITY_Chat and CTO Agent|Chat and CTO Agent]]
- [[_COMMUNITY_Position Monitoring and Logging|Position Monitoring and Logging]]
- [[_COMMUNITY_Discord Command Interface|Discord Command Interface]]
- [[_COMMUNITY_Alpaca API Client|Alpaca API Client]]
- [[_COMMUNITY_Journal Agent and Cron Config|Journal Agent and Cron Config]]
- [[_COMMUNITY_Options Analysis and Greeks|Options Analysis and Greeks]]
- [[_COMMUNITY_Trade Execution Engine|Trade Execution Engine]]
- [[_COMMUNITY_Backtester and Journal Loader|Backtester and Journal Loader]]
- [[_COMMUNITY_Pre-Trade Context Builder|Pre-Trade Context Builder]]
- [[_COMMUNITY_Signal Correlation Analysis|Signal Correlation Analysis]]
- [[_COMMUNITY_Error Detection and Recovery|Error Detection and Recovery]]
- [[_COMMUNITY_Position Exit and PnL|Position Exit and PnL]]
- [[_COMMUNITY_CTO Tech Intelligence|CTO Tech Intelligence]]
- [[_COMMUNITY_Ticker Discovery and Market Data|Ticker Discovery and Market Data]]
- [[_COMMUNITY_Macro and Economic Context|Macro and Economic Context]]
- [[_COMMUNITY_CTO Task Runner|CTO Task Runner]]
- [[_COMMUNITY_Pipeline Gate Checks|Pipeline Gate Checks]]
- [[_COMMUNITY_Log Error Pattern Detector|Log Error Pattern Detector]]
- [[_COMMUNITY_Market Sentiment Indicators|Market Sentiment Indicators]]
- [[_COMMUNITY_CTO Weekly Report|CTO Weekly Report]]
- [[_COMMUNITY_Dark Pool and Flow Detector|Dark Pool and Flow Detector]]
- [[_COMMUNITY_Google Sheets Sync|Google Sheets Sync]]
- [[_COMMUNITY_Heartbeat Monitor|Heartbeat Monitor]]
- [[_COMMUNITY_KARNA Infrastructure Overview|KARNA Infrastructure Overview]]
- [[_COMMUNITY_Design Principles and Config|Design Principles and Config]]
- [[_COMMUNITY_Error Catalog CLI|Error Catalog CLI]]
- [[_COMMUNITY_Discord Setup and Channels|Discord Setup and Channels]]
- [[_COMMUNITY_SOFI Collar Strategy|SOFI Collar Strategy]]
- [[_COMMUNITY_Live Trading Readiness|Live Trading Readiness]]
- [[_COMMUNITY_System Test Suite|System Test Suite]]
- [[_COMMUNITY_Security Rules and File Paths|Security Rules and File Paths]]
- [[_COMMUNITY_Research Agent Manual|Research Agent Manual]]
- [[_COMMUNITY_Decision Logger|Decision Logger]]
- [[_COMMUNITY_QuantAI System Overview|QuantAI System Overview]]
- [[_COMMUNITY_Roadmap Current State|Roadmap Current State]]
- [[_COMMUNITY_Setup Guide|Setup Guide]]
- [[_COMMUNITY_VPS Setup|VPS Setup]]
- [[_COMMUNITY_Git Rules|Git Rules]]
- [[_COMMUNITY_Prevent Accidental Damage|Prevent Accidental Damage]]
- [[_COMMUNITY_Journal Stats Commands|Journal Stats Commands]]
- [[_COMMUNITY_Journal Weekly Digest|Journal Weekly Digest]]
- [[_COMMUNITY_Research Agent Soul|Research Agent Soul]]
- [[_COMMUNITY_Cron Pipeline Schedule|Cron Pipeline Schedule]]
- [[_COMMUNITY_aiohttp Dependency|aiohttp Dependency]]
- [[_COMMUNITY_APScheduler Dependency|APScheduler Dependency]]
- [[_COMMUNITY_uvicorn Dependency|uvicorn Dependency]]
- [[_COMMUNITY_Pydantic Dependency|Pydantic Dependency]]
- [[_COMMUNITY_Environment Variables|Environment Variables]]

## God Nodes (most connected - your core abstractions)
1. `post()` - 25 edges
2. `run_guard_pipeline()` - 25 edges
3. `TradeProposal` - 23 edges
4. `run()` - 18 edges
5. `build_context()` - 18 edges
6. `run_weekly_scan()` - 18 edges
7. `run_entry()` - 17 edges
8. `post_to_discord()` - 16 edges
9. `TestPositionGuards` - 16 edges
10. `build_context()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `discord.py` --conceptually_related_to--> `Infra Agent`  [INFERRED]
  discord-bot/requirements.txt → v2/workspace-infra/AGENTS.md
- `yfinance` --conceptually_related_to--> `Market Intelligence`  [INFERRED]
  orchestrator/requirements.txt → v2/workspace-infra/AGENTS.md
- `finnhub-python` --conceptually_related_to--> `Market Intelligence`  [INFERRED]
  orchestrator/requirements.txt → v2/workspace-infra/AGENTS.md
- `alpaca-py` --conceptually_related_to--> `autonomous_execution.py`  [INFERRED]
  orchestrator/requirements.txt → v2/workspace-infra/AGENTS.md
- `main()` --calls--> `run()`  [INFERRED]
  /home/trader/QuantAI/discord-bot/bot.py → /home/trader/QuantAI/v2/shared-data/scripts/autonomous_execution.py

## Hyperedges (group relationships)
- **Autonomous Pipeline Script Chain** — system_state_run_pipeline, system_state_market_intelligence, system_state_scan_options, system_state_debate_chamber, system_state_autonomous_execution, system_state_sheets_sync [EXTRACTED 1.00]
- **Four Discord Agents** — orchestrator_agents_operating_manual, research_agents_operating_manual, journal_agents_operating_manual, system_state_four_agents [EXTRACTED 1.00]
- **Agent Alpha and Beta Trading Strategy** — tutorial_agent_alpha, tutorial_agent_beta, system_state_agent_alpha, system_state_agent_beta, orchestrator_soul_alpha_config, orchestrator_soul_beta_config [EXTRACTED 1.00]
- **SOFI Collar Strategy System** — tutorial_sofi_collar, system_state_sofi_collar, research_agents_trigger_actions, research_agents_position_monitoring, orchestrator_agents_operating_manual [EXTRACTED 1.00]
- **Guard Rule Enforcement Across System** — tutorial_guard_rules, system_state_guard_rules, orchestrator_agents_guard_rules, setup_guide_guard_engine, readme_three_laws [EXTRACTED 1.00]
- **Trade Journal System** — system_state_trade_journal, journal_agents_operating_manual, journal_agents_logging, journal_agents_closing, journal_agents_stats, system_state_sheets_sync, tutorial_google_sheets [EXTRACTED 1.00]
- **v2 Pipeline Core Scripts** — workspace_infra_agents_run_pipeline, workspace_infra_agents_market_intelligence, workspace_infra_agents_scan_options, workspace_infra_agents_debate_chamber, workspace_infra_agents_autonomous_execution [EXTRACTED 1.00]
- **Alpaca mleg API Bug Cluster** — quantai_knowledge_bug_mleg_qty, quantai_knowledge_bug_position_intent, quantai_knowledge_alpaca_gotchas, runbook_mleg_order_fail_runbook, runbook_mleg_order_fail_payload_structure [INFERRED 0.90]
- **Phase E Self-Learning Error System Components** — quantai_knowledge_self_learning_error_system, quantai_knowledge_error_catalog, quantai_knowledge_error_detector, quantai_knowledge_error_learner, quantai_knowledge_add_error_cli, quantai_knowledge_rationale_phase_e [EXTRACTED 1.00]
- **All Runbooks** — runbook_market_intelligence_fail_runbook, runbook_mleg_order_fail_runbook, runbook_chain_404_runbook, runbook_debit_credit_rejection_runbook, runbook_pipeline_silent_fail_runbook, runbook_alpaca_connection_runbook, runbook_scan_timeout_runbook [EXTRACTED 1.00]
- **Trading Agent Strategies** — quantai_knowledge_agent_alpha_strategy, quantai_knowledge_agent_beta_strategy, workspace_infra_agents_agent_alpha, workspace_infra_agents_agent_beta, quantai_knowledge_guard_rules [EXTRACTED 1.00]

## Communities

### Community 0 - "Guard Engine Rule Checks"
Cohesion: 0.04
Nodes (49): BaseModel, check_bull_put(), check_cooldown(), check_covered_call(), check_daily_loss(), check_earnings_blackout(), check_halted(), check_iron_condor() (+41 more)

### Community 1 - "Dependencies and Strategy Config"
Cohesion: 0.04
Nodes (66): discord.py, py_vollib, FastAPI (guard-engine dep), alpaca-py, finnhub-python, yfinance, add_error.py CLI, Agent Alpha Strategy (+58 more)

### Community 2 - "System Health Checks"
Cohesion: 0.06
Nodes (60): build_health_embeds(), build_startup_embed(), check_agent_journals(), check_alpaca_connection(), check_cache_freshness(), check_config_sanity(), check_data_quality(), check_discord_webhooks() (+52 more)

### Community 3 - "Chat and CTO Agent"
Cohesion: 0.06
Nodes (59): chat_with_claude(), ChatAgent, cmd_decide(), cmd_remember(), handle_cto_task(), handle_lessons(), handle_remember(), handle_stats() (+51 more)

### Community 4 - "Position Monitoring and Logging"
Cohesion: 0.07
Nodes (57): _close_position(), _estimate_condor_current_cost(), guard_check(), load_params(), log_trade(), make_embed(), monitor_positions(), post_discord() (+49 more)

### Community 5 - "Discord Command Interface"
Cohesion: 0.08
Nodes (54): ChannelRouter, cmd_analyze(), cmd_brief(), cmd_emergency_stop(), cmd_guard_check(), cmd_resume(), cmd_rules(), cmd_status() (+46 more)

### Community 6 - "Alpaca API Client"
Cohesion: 0.07
Nodes (47): cancel_all_orders(), cancel_order(), close_all_positions(), close_position(), _generate_hash(), get_account(), get_data_client(), get_latest_quote() (+39 more)

### Community 7 - "Journal Agent and Cron Config"
Cohesion: 0.05
Nodes (48): Alpaca API Gotchas (mleg qty, no position_intent), Cron Schedule, Journal Agent Alpha/Beta Trade Tracking, Journal Agent Trade Closing Protocol, Journal Agent Trade Logging Protocol, Journal Agent Operating Manual, Journal Agent Core Belief (journal as most valuable asset), Journal Agent Soul (trading diary identity) (+40 more)

### Community 8 - "Options Analysis and Greeks"
Cohesion: 0.08
Nodes (40): analyze_bull_put_spread(), analyze_covered_call(), analyze_iron_condor(), analyze_option(), classify_moneyness(), compute_greeks(), compute_iv(), dte_to_years() (+32 more)

### Community 9 - "Trade Execution Engine"
Cohesion: 0.11
Nodes (38): build_occ_symbol(), check_guards(), count_open_agent_trades(), execute_bear_call_spread(), execute_bull_put_spread(), execute_diagonal_spread(), execute_generic_spread(), execute_iron_condor() (+30 more)

### Community 10 - "Backtester and Journal Loader"
Cohesion: 0.1
Nodes (34): backtest_agent1_params(), backtest_agent2_params(), get_trade_pairs(), load_journal(), backtester.py — Param Change Validator ========================================, Re-simulate a historical Agent 1 trade with different params.     Returns what t, Backtest Agent 1 param change against last N days of journal data.     Returns v, Backtest Agent 2 param change. Uses 60 days (longer because CC trades are weekly (+26 more)

### Community 11 - "Pre-Trade Context Builder"
Cohesion: 0.08
Nodes (34): build_context(), build_context_embed(), _build_summary(), _cache_path(), _get_agent1_params(), _get_agent2_params(), main(), context_builder.py — Pre-Trade Context Score (0–100) =========================== (+26 more)

### Community 12 - "Signal Correlation Analysis"
Cohesion: 0.08
Nodes (31): analyze_overall_correlation(), analyze_per_signal_correlation(), build_correlation_embed(), _build_summary(), load_context_scores(), load_trade_outcomes(), load_weights(), match_scores_to_outcomes() (+23 more)

### Community 13 - "Error Detection and Recovery"
Cohesion: 0.16
Nodes (25): action_restart_service(), action_retry(), classify_lines(), handle_known(), handle_unknown(), line_matches_entry(), load_catalog(), load_dedup() (+17 more)

### Community 14 - "Position Exit and PnL"
Cohesion: 0.19
Nodes (22): build_closing_legs(), build_occ(), check_exit_threshold(), compute_trade_pnl(), fetch_alpaca_positions(), hdrs(), load_journal(), log() (+14 more)

### Community 15 - "CTO Tech Intelligence"
Cohesion: 0.13
Nodes (20): handle_cto_scan(), On-demand CTO tech intelligence scan., analyze_findings(), build_cto_scan_embeds(), main(), _parse_arxiv_entries(), cto_agent.py — CTO Tech Intelligence Agent =====================================, Fetch recent papers from arXiv in quantitative finance categories.     arXiv API (+12 more)

### Community 16 - "Ticker Discovery and Market Data"
Cohesion: 0.24
Nodes (16): discover_tickers(), get_avg_volume(), get_earnings(), get_iv_rank(), get_price(), get_technicals(), Pull liquid, optionable tickers from multiple sources., Poor man's covered call/put — diagonal spread.     Sell near-term option, buy fu (+8 more)

### Community 17 - "Macro and Economic Context"
Cohesion: 0.23
Nodes (17): _cache_path(), fetch_fred_series(), get_economic_calendar(), get_fred_macro(), get_macro_context(), get_news_sentiment(), _get_watchlist_earnings_calendar(), main() (+9 more)

### Community 18 - "CTO Task Runner"
Cohesion: 0.16
Nodes (17): build_prompt(), main(), post_discord(), process_task(), cto_listener.py — Dockerized CTO Task Runner ===================================, Read the queue file. Returns the task dict if status=pending,     else None. Mat, Update the queue file's status field., Build the Claude Code prompt, matching the bash script's format. (+9 more)

### Community 19 - "Pipeline Gate Checks"
Cohesion: 0.19
Nodes (10): count_open_agent_trades(), load_intel(), log(), Write UTC timestamp to pipeline beat file so heartbeat_monitor can check livenes, run_entry(), run_eod(), run_monitor(), run_script() (+2 more)

### Community 20 - "Log Error Pattern Detector"
Cohesion: 0.24
Nodes (15): line_signature(), load_catalog(), log(), looks_like_error(), main(), match_catalog(), now_et(), post_discord() (+7 more)

### Community 21 - "Market Sentiment Indicators"
Cohesion: 0.24
Nodes (15): _cache_path(), get_fear_greed(), get_put_call_ratio(), get_sentiment_context(), get_vix_term_structure(), main(), _parse_cboe_pcr(), sentiment_data.py — Market Sentiment Indicators ================================ (+7 more)

### Community 22 - "CTO Weekly Report"
Cohesion: 0.18
Nodes (14): build_cto_embeds(), collect_agent_stats(), collect_cache_health(), collect_eod_scores(), collect_system_metrics(), generate_cto_report(), main(), cto_report.py — Weekly CTO Report ==================================== Runs ever (+6 more)

### Community 23 - "Dark Pool and Flow Detector"
Cohesion: 0.23
Nodes (13): _build_flow_summary(), _cache_path(), detect_dark_pool_activity(), detect_unusual_options_activity(), flow_detector.py — Unusual Options Activity Detector (Free) ====================, Dark pool proxy: abnormal equity volume vs 20-day average.      Real dark pool d, Complete flow scan for a symbol.     Combines Vol/OI unusual activity + dark poo, Scan all watchlist symbols for dark pool activity.     Called by Agent 2 every M (+5 more)

### Community 24 - "Google Sheets Sync"
Cohesion: 0.36
Nodes (10): bold_header(), color_rows_by_status(), ensure_sheets(), get_sheet_id_by_name(), load_trades(), Green for CLOSED wins, red for CLOSED losses, yellow for OPEN., setup(), sync() (+2 more)

### Community 25 - "Heartbeat Monitor"
Cohesion: 0.52
Nodes (6): cooldown_ok(), is_market_hours(), main(), post_discord(), read_beat(), record_cooldown()

### Community 26 - "KARNA Infrastructure Overview"
Cohesion: 0.29
Nodes (7): Architecture Overview (KARNA, QuantAI Pipeline, LiteLLM, Dashboard), KARNA + QuantAI Project Overview, Rationale: Never Modify openclaw.service (Type=simple), README Architecture Diagram, Docker Deploy and Test, VPS Infrastructure (Hetzner CX31), sync_workspaces.sh

### Community 28 - "Design Principles and Config"
Cohesion: 0.5
Nodes (4): Design Principles (LLMs for judgment, cost-conscious, data sovereignty), Rationale: Design Principles Trade-offs, API Keys Configuration, Data Sources (yfinance, Finnhub, Alpha Vantage, Alpaca)

### Community 29 - "Error Catalog CLI"
Cohesion: 1.0
Nodes (2): main(), parse_args()

### Community 30 - "Discord Setup and Channels"
Cohesion: 0.67
Nodes (3): Discord Server and Bot Setup, Four Agents (Orchestrator, Research, Infra, Journal), Discord Channels (#chat, #research, #infra, #journal)

### Community 31 - "SOFI Collar Strategy"
Cohesion: 0.67
Nodes (3): SOFI 5 Trigger Actions, SOFI Collar (200 paper shares), SOFI Collar Strategy

### Community 32 - "Live Trading Readiness"
Cohesion: 0.67
Nodes (3): Orchestrator Soul: What Winning Looks Like, Success Metrics (win rate targets, live capital), Pre-Live Checklist

### Community 35 - "System Test Suite"
Cohesion: 1.0
Nodes (2): Script Inventory, system_test.py (43-check health test)

### Community 36 - "Security Rules and File Paths"
Cohesion: 1.0
Nodes (2): Key File Paths, Security Rules (never expose .env, credentials)

### Community 37 - "Research Agent Manual"
Cohesion: 1.0
Nodes (2): Research Agent Operating Manual, Research Agent Core Beliefs (edge via research)

### Community 40 - "Decision Logger"
Cohesion: 1.0
Nodes (1): Log a decision. Categories: rule_change, strategy, architecture, bug_fix, config

### Community 41 - "QuantAI System Overview"
Cohesion: 1.0
Nodes (1): QuantAI System Overview

### Community 42 - "Roadmap Current State"
Cohesion: 1.0
Nodes (1): Roadmap Current State (Paper Trading Live)

### Community 43 - "Setup Guide"
Cohesion: 1.0
Nodes (1): Setup Guide Overview

### Community 44 - "VPS Setup"
Cohesion: 1.0
Nodes (1): Hetzner VPS Setup

### Community 45 - "Git Rules"
Cohesion: 1.0
Nodes (1): Git Rules (local main is source of truth)

### Community 46 - "Prevent Accidental Damage"
Cohesion: 1.0
Nodes (1): Prevent Accidental Damage Rules

### Community 47 - "Journal Stats Commands"
Cohesion: 1.0
Nodes (1): Journal Agent Stats Commands

### Community 48 - "Journal Weekly Digest"
Cohesion: 1.0
Nodes (1): Journal Agent Weekly Digest Format

### Community 49 - "Research Agent Soul"
Cohesion: 1.0
Nodes (1): Research Agent Soul (intelligence engine)

### Community 50 - "Cron Pipeline Schedule"
Cohesion: 1.0
Nodes (1): Cron Pipeline

### Community 51 - "aiohttp Dependency"
Cohesion: 1.0
Nodes (1): aiohttp (orchestrator dep)

### Community 52 - "APScheduler Dependency"
Cohesion: 1.0
Nodes (1): apscheduler

### Community 53 - "uvicorn Dependency"
Cohesion: 1.0
Nodes (1): uvicorn

### Community 54 - "Pydantic Dependency"
Cohesion: 1.0
Nodes (1): pydantic

### Community 55 - "Environment Variables"
Cohesion: 1.0
Nodes (1): Environment Variables

## Knowledge Gaps
- **325 isolated node(s):** `Green for CLOSED wins, red for CLOSED losses, yellow for OPEN.`, `Post a message to Discord via bot token. Falls back to webhook if set.`, `Post a trade execution alert to #alerts channel.`, `OCC format: SYMBOL + YYMMDD + C/P + 8-digit strike (strike*1000, zero-padded)`, `Convert '7DTE', '0DTE' etc to YYYY-MM-DD. Never returns today.` (+320 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Error Catalog CLI`** (3 nodes): `main()`, `parse_args()`, `add_error.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `System Test Suite`** (2 nodes): `Script Inventory`, `system_test.py (43-check health test)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Security Rules and File Paths`** (2 nodes): `Key File Paths`, `Security Rules (never expose .env, credentials)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Research Agent Manual`** (2 nodes): `Research Agent Operating Manual`, `Research Agent Core Beliefs (edge via research)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Decision Logger`** (1 nodes): `Log a decision. Categories: rule_change, strategy, architecture, bug_fix, config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `QuantAI System Overview`** (1 nodes): `QuantAI System Overview`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Roadmap Current State`** (1 nodes): `Roadmap Current State (Paper Trading Live)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Setup Guide`** (1 nodes): `Setup Guide Overview`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `VPS Setup`** (1 nodes): `Hetzner VPS Setup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Git Rules`** (1 nodes): `Git Rules (local main is source of truth)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Prevent Accidental Damage`** (1 nodes): `Prevent Accidental Damage Rules`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Journal Stats Commands`** (1 nodes): `Journal Agent Stats Commands`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Journal Weekly Digest`** (1 nodes): `Journal Agent Weekly Digest Format`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Research Agent Soul`** (1 nodes): `Research Agent Soul (intelligence engine)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Cron Pipeline Schedule`** (1 nodes): `Cron Pipeline`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `aiohttp Dependency`** (1 nodes): `aiohttp (orchestrator dep)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `APScheduler Dependency`** (1 nodes): `apscheduler`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `uvicorn Dependency`** (1 nodes): `uvicorn`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Pydantic Dependency`** (1 nodes): `pydantic`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Environment Variables`** (1 nodes): `Environment Variables`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `post()` connect `Position Monitoring and Logging` to `System Health Checks`, `Chat and CTO Agent`, `Discord Command Interface`, `Alpaca API Client`, `Trade Execution Engine`, `Backtester and Journal Loader`, `Signal Correlation Analysis`, `Position Exit and PnL`, `CTO Tech Intelligence`, `CTO Task Runner`, `CTO Weekly Report`, `Heartbeat Monitor`?**
  _High betweenness centrality (0.264) - this node is a cross-community bridge._
- **Why does `run_entry()` connect `Position Monitoring and Logging` to `System Health Checks`, `Chat and CTO Agent`, `Dark Pool and Flow Detector`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Why does `get_options_chain()` connect `Position Monitoring and Logging` to `Pre-Trade Context Builder`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `post()` (e.g. with `post_discord()` and `place_mleg_order()`) actually correct?**
  _`post()` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `run_guard_pipeline()` (e.g. with `test_clean_trade_approves()` and `.test_halted_rejects()`) actually correct?**
  _`run_guard_pipeline()` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 20 inferred relationships involving `TradeProposal` (e.g. with `TestPositionGuards` and `TestPortfolioGuards`) actually correct?**
  _`TradeProposal` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `run()` (e.g. with `action_retry()` and `action_restart_service()`) actually correct?**
  _`run()` has 8 INFERRED edges - model-reasoned connections that need verification._