# QuantAI — System State
**Last updated: March 28, 2026 | v2 — OpenClaw multi-agent + SOFI collar**

Start every new chat with: "Read SYSTEM_STATE.md."

---

## Infrastructure

| Component | Value |
|---|---|
| VPS | Hetzner CX31 · 87.99.141.55 |
| OS | Ubuntu 24 |
| Repo | github.com/amitc3353/QuantAI (branch: `v2-openclaw`) |
| Runtime | **OpenClaw 2026.3.23-2** (single Node.js process, multi-agent) |
| Trading mode | **PAPER** |
| Strategy | **SOFI Collar** |

**4 agents (each has own Discord bot + isolated workspace):**
| Agent | Channel | Model | Role |
|---|---|---|---|
| Orchestrator | #chat | Sonnet | Primary interface, routes tasks |
| Research | #research | Sonnet | Daily SOFI analysis, data fetching |
| Infra | #infra | Sonnet | System health, GitHub, code fixes |
| Journal | #journal | Haiku | Trade logging, stats, digests |

---

## Strategy: SOFI Collar

**Config:** `v2/shared-data/strategies/sofi_collar.json`

| | Detail |
|---|---|
| Stock | SOFI Technologies (~$15) |
| Shares | 200 paper → 1000 long term |
| Capital | ~$3,000 |
| SELL | $16 calls biweekly → +$110/cycle |
| BUY | $12 puts monthly → -$50 |
| Net income | +$170/month |
| Max loss | $600 |

**Pre-decided trigger actions:**
- $15.70 → Monitor | $16.00 → Roll call to $18 | Called away → Accept profit
- $12.50 → Monitor | $12.00 → Exercise put or roll to $10

**Timeline:** 4 weeks paper → real money $3K → scale to 1000 shares

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| yfinance | SOFI price, volume, technicals, IV, options chain | Free |
| Finnhub | News, earnings, insider activity | Free tier |
| Alpha Vantage | Supplementary quotes | Free (25/day) |
| Alpaca | Paper trading execution | Free |

---

## Scheduled Jobs

| Time (ET) | Agent | Job |
|---|---|---|
| 6:30 AM Mon-Fri | Research | Daily SOFI brief → #research |
| 8:00 AM Mon-Fri | Infra | Health check → #infra |
| 4:30 PM Friday | Journal | Weekly digest → #journal |

---

## File Structure

```
v2/                             ← Active system
├── setup.sh                    ← VPS setup script
├── openclaw.config.js          ← Multi-agent config
├── .env.example                ← Environment template
├── DISCORD_SETUP.md            ← Bot creation guide
├── workspace-{orchestrator,research,infra,journal}/
│   ├── AGENTS.md               ← Agent operating manual
│   └── SOUL.md                 ← Agent personality
└── shared-data/
    ├── strategies/sofi_collar.json
    ├── journal/{paper,real}/trades.jsonl
    ├── cache/sofi_latest.json
    └── scripts/fetch_sofi.py

v1-archive/                     ← Old Docker system (reference)
configs/                        ← Old v1 configs
data/                           ← Old v1 data
```

---

## Security

- OpenClaw pinned to `2026.3.23-2` (all 2026 CVEs patched)
- Gateway port 18789 firewalled
- Zero ClawHub skills (supply chain risk)
- GitHub PAT rotated per session
- Agent tools restricted per role

---

## Monthly Cost

| Item | Cost |
|---|---|
| Claude API (3× Sonnet + 1× Haiku) | ~$15-25/mo |
| VPS | ~$12/mo |
| Data | $0 |
| **Total** | **~$27-37/mo** |

---

*v2 migration: Docker 5-container → OpenClaw single process. SPY iron condors → SOFI collar.*
