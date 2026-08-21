# QuantAI — System State
**Last updated: 2026-05-03**

Start every new chat: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI (local `main` is source of truth) |
| Repo path on VPS | /home/trader/QuantAI |
| OpenClaw gateway | /root/quantai-v2/ |
| Trading mode | PAPER |
| Paper account | IBKR DUP851506 — equity ~$1,000,000 |
| Active broker | IBKR (port 4002, ib_insync) — Alpaca paper retained as fallback |

**Two directory trees — both matter:**
- `/home/trader/QuantAI/` — git repo, all Python scripts (cron runs from here)
- `/root/quantai-v2/` — OpenClaw reads agent workspaces here, all runtime data written here

---

## How This System Works

Three autonomous trading agents (Alpha, Beta, Gamma) + Sentinel (operations agent) run on cron. OpenClaw provides Discord-side conversational agents that read state and answer questions. There are no manual trades — everything is autonomous within hard guardrails.

---

## The Trading Agents

| Agent | Cadence | Universe | Strategies | Journal IDs |
|---|---|---|---|---|
| **Alpha** | every 15 min, 9–4 ET | ETF + equity options (78+ tickers) | Bull put, bear call, iron condor, iron butterfly, jade lizard, calendar, diagonal | `A###` |
| **Beta** | every 15 min, 9–4 ET | SPX / XSP / VIX index options | 12-regime classifier → 8 strategy modules (event strangle, ratios, BWB, vix calls, etc.) | `B###` |
| **Gamma** | scan 4:30 PM ET, execute 9:33 AM ET | Equity options on RSI(10) watchlist | RSI mean-reversion (Connors method) | `G###` |

All three never attempt strategies that require owning shares (covered calls, collars, cash-secured puts, covered strangles). The `REQUIRES_SHARES` defensive guard in `autonomous_execution.py` blocks these unconditionally.

---

## OpenClaw Conversational Agents (Discord)

| Agent | Channel | Model | Role |
|---|---|---|---|
| Orchestrator | #chat | Claude Sonnet | Primary interface — analysis, agent status, scoring |
| Research | #research | Claude Sonnet | Spread reports, regime briefs, scan output |
| Infra | #infra | Claude Sonnet | Health checks, cron, debugging |
| Journal | #journal | Claude Haiku | Trade activity, stats, Google Sheets sync |

These agents READ state (logs, journal, dashboard) and answer questions. They do not place trades.

---

## Sentinel (operations agent)

A first-class agent (peer of Alpha/Beta/Gamma) that watches the system and self-heals. Schedule (ET):

| Time | Mode | Purpose |
|---|---|---|
| 8:30 AM | apply | Pre-market: tests, errors, broker, dashboard |
| 10/11/12/1/2/3 PM | observe | Hourly market check; silent unless critical |
| 4:15 PM | apply | Post-close: drain digest, reclassify errors, summary |
| 9 PM | observe | Evening: nightly maintenance prep, ibgateway health |
| 10 AM Sat & Sun | observe | Weekend coverage; silent unless something is down |

Built-in safe_auto: errors.db catalog reclassification of known-noise patterns runs every apply cycle. Risky proposals (code edits, ibgateway restart with positions open) post to `#karna-approvals` for ✅/❌ approval from Discord.

**Code-enforced safety rails** (NOT LLM-overrideable):
- Trading-path scripts (`autonomous_execution.py`, `beta_agent.py`, `gamma_agent.py`, `position_monitor.py`, `_broker_ibkr.py`, `broker.py`) — never edited
- `.env`, openclaw, journal — never touched
- ibgateway — restartable only when market closed AND 0 open positions
- openclaw — never restarted

---

## Autonomous Pipeline (Cron)

```
*/15 13-20 * * 1-5   run_pipeline.py             → Alpha pipeline, every 15 min
5    20    * * 1-5   run_pipeline.py eod         → 4:05 PM ET, daily wrap
30   13    * * 1-5   pre_trade_check.py          → 9:30 AM ET pre-flight
*/15 13-20 * * 1-5   beta_agent.py               → Beta, every 15 min
30   20    * * 1-5   gamma_agent.py --scan       → 4:30 PM ET, build watchlist
33   13    * * 1-5   gamma_agent.py --execute    → 9:33 AM ET, fire entries
*/2  13-20 * * 1-5   position_monitor.py         → market-hours position health
*/2  *     * * *     heartbeat_monitor.py        → 24/7 IBKR + pipeline beat probe
*/2  *     * * *     system_monitor.py           → Sentinel's eyes, 13-check report
*/15 12-21 * * 1-5   sentinel_agent.py --auto    → wrapper-driven
0    22    * * 5     error_learner.py            → Friday weekly error catalog learn
45   20    * * 5     weekly_synthesis.py         → Friday post-close synthesis
```

**Decision logic every 15 min (run_pipeline.py):**
1. Market open (9:30–4 PM ET weekdays)? → else exit
2. Opening volatility window (9:30–9:45)? → wait, no entry
3. Past 3 PM? → monitor only, no new entries
4. Past 3:30 PM? → hard close all agent positions
5. VIX ≥ 35 or regime=halt? → no entry
6. Daily entry limit hit? → monitor only
7. All clear → intelligence → scan → debate → execute

---

## Guard Rules — Always Enforced

| Rule | Value |
|---|---|
| Max loss per trade | 2% of position-sizing cap |
| Position-sizing cap (Alpha) | $50,000 |
| Earnings blackout | 14 days |
| VIX ≥ 35 | No new trades |
| No-trade windows | 9:30–9:45 AM, 3:45–4:00 PM ET |
| Stop loss | 2× credit |
| Profit target | 50% of max profit |
| Max simultaneous agent positions | 3 per agent |
| Entry cutoff | 3:00 PM ET |
| Hard close | 3:30 PM ET |
| Strategy gate | `REQUIRES_SHARES` blocks covered calls / collars / CSPs / covered strangles |

---

## Scripts

All at `/home/trader/QuantAI/v2/shared-data/scripts/`. Runtime data at `/root/quantai-v2/shared-data/`.

| Script | Purpose |
|---|---|
| `run_pipeline.py` | Master cron entry point — conditions → entry/monitor/eod |
| `market_intelligence.py` | Intelligence packet: VIX, technicals, events, earnings |
| `debate_chamber.py` | Bull/Bear/Judge → top 2 trade proposals (Alpha) |
| `autonomous_execution.py` | Places IBKR mleg orders, journals, syncs sheets |
| `beta_agent.py` | Regime classifier + 8 strategy modules (Beta) |
| `gamma_agent.py` | RSI(10) mean-reversion (Gamma) |
| `position_monitor.py` | Position health, ghost reconciliation, exit triggers |
| `_broker_ibkr.py` / `broker.py` | IBKR adapter (ib_insync) |
| `heartbeat_monitor.py` | IBKR port + pipeline beat probe (24/7) |
| `system_monitor.py` | Sentinel's deterministic 13-check report |
| `sentinel_agent.py` | Sentinel: LLM-powered ops agent |
| `agent_self_diagnosis.py` | Post-close: 3-gap diagnosis per closed trade |
| `trade_reviewer.py` | Post-close: thesis review, lessons, parameter suggestions |
| `weekly_synthesis.py` | Friday: aggregate week's diagnoses + reviews into per-agent report |
| `scan_options.py` | Dynamic scanner: credit spreads + diagonals + iron condors |
| `error_detector.py` / `error_learner.py` / `collect_errors.py` | Error pipeline |
| `eod_summary.py` | Daily Discord summary at 4:05 PM ET |
| `sheets_sync.py` | Syncs trades.jsonl → Google Sheet (3 tabs) |
| `pattern_engine.py` | Statistical patterns from 20+ closed trades |
| `system_test.py` | **44-check health test — run weekly** |

---

## System Health Check

```bash
sudo python3 /home/trader/QuantAI/v2/shared-data/scripts/system_test.py
```

Expected: **44/44 passed**.

For live deterministic state without running anything:

```bash
sudo cat /var/dashboard/state/system-health-report.json | jq '.status, .data.checks'
```

13 checks: ibkr_port, litellm_4000, clawroute_18790, cron_freshness, disk, memory, self_learning_sla, weekly_synthesis, collector_staleness, journal_schema, test_results, graphify, open_positions.

---

## Google Sheets Journal

URL: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0

| Tab | Contents |
|---|---|
| All Trades | Every entry in trades.jsonl, color-coded |
| Agent Trades | All trades from agent_alpha / agent_beta / agent_gamma |
| Summary | Live win rate, P&L, open count |

Trade IDs: `A###` (Alpha), `B###` (Beta), `G###` (Gamma). Auto-syncs after every execution.

---

## Self-Learning Pipeline

After a trade closes, `position_monitor.py` triggers two LLM calls:
1. `agent_self_diagnosis.diagnose(trade_id)` → identifies up to 3 capability gaps → writes `/root/quantai-v2/shared-data/capability_requests/{agent}/{trade_id}.json`
2. `trade_reviewer.review(trade_id)` → thesis outcome, lessons, parameter suggestions → writes `/root/quantai-v2/shared-data/trade_reviews/{agent}/{trade_id}.md`

Friday 4:45 PM ET: `weekly_synthesis.py` aggregates the week's diagnoses + reviews into a per-agent report at `/root/quantai-v2/shared-data/weekly_reports/{week}_synthesis.md`, posts a digest to `#alerts`.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| IBKR Gateway (ib_insync) | Order execution, positions, options chain | Free (paper) |
| yfinance | Price, technicals, VIX, fundamentals | Free |
| Finnhub | Events, earnings, news | Free tier |
| Alpha Vantage | Earnings surprises | Free (25/day) |
| Google Sheets API | Journal sync | Free |
| ClawRoute → Anthropic | All LLM reasoning (Sonnet/Haiku tiers) | ~$25–40/mo |

---

## Key File Paths

```
/root/quantai-v2/
  workspace-{orchestrator,research,infra,journal}/
    SOUL.md + AGENTS.md          ← OpenClaw agent instructions (edit here for live effect)
  shared-data/
    journal/paper/trades.jsonl   ← All paper trades (source of truth, NEVER mutate)
    capability_requests/{agent}/  ← Per-trade self-diagnosis JSON
    trade_reviews/{agent}/        ← Per-trade markdown reviews
    weekly_reports/               ← Friday synthesis reports
    cache/                        ← Intelligence + debate output
    logs/                         ← pipeline / heartbeat / position_monitor / sentinel
    google_service_account.json   ← Google Sheets auth (never commit)

/home/trader/QuantAI/
  v2/shared-data/scripts/         ← All Python scripts (git-tracked)
  v2/shared-data/agents/
    AGENT_{ALPHA,BETA,GAMMA,SENTINEL}_IDENTITY.md
    skills/                       ← Modular knowledge files
  v2/shared-data/tests/           ← pytest suite (~640+ tests)
  docs/error-catalog.json         ← 60-entry error taxonomy
  docs/runbooks/                  ← Per-error remediation procedures
  .env                            ← All API keys
```

---

## Pre-Live Checklist

- [ ] Agent Alpha win rate ≥ 60% over 40+ closed trades
- [ ] Agent Beta win rate ≥ 65% over 40+ closed trades
- [ ] Agent Gamma performance baseline (30+ trades minimum)
- [ ] system_test.py: 44/44 consistently
- [ ] No weekly drawdown > $5,000 in last 4 weeks
- [ ] place_mleg_order partial-fill safeguard verified end-to-end
- [ ] Sentinel: zero quarantined fixes for 30 days
- [ ] Open IBKR live account (paper account graduates)

---

## Security

- All API keys in `/home/trader/QuantAI/.env` only — never read or print
- `google_service_account.json` — never commit to git
- IBKR credentials live in `.env`, injected at runtime via systemd

---

## Starting a New Chat

> "Read SYSTEM_STATE.md. I want to [task]."

After workspace edits: `bash /home/trader/QuantAI/scripts/sync_workspaces.sh`

---

*Last updated: 2026-05-03 — manual trading workflow retired (system fully autonomous: Alpha + Beta + Gamma + Sentinel). 44/44 system test passes. 641+ pytest passing. IBKR active. Sentinel watching.*
