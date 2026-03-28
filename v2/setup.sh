#!/bin/bash
# =============================================================================
# QuantAI v2 — ONE-SHOT SETUP SCRIPT
# =============================================================================
# 
# SSH into your VPS and run this ONE command:
#   curl -sSL https://raw.githubusercontent.com/amitc3353/QuantAI/main/v2/setup.sh | bash
#
# OR paste this entire script into your terminal.
#
# BEFORE RUNNING: Have these ready:
#   - Your Anthropic API key
#   - Your Alpaca paper API key + secret
#   - 4 Discord bot tokens (create them first, see instructions below)
#   - Discord server + channel IDs
#
# =============================================================================

set -e

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   QuantAI v2 — OpenClaw Setup       ║"
echo "║   SOFI Collar Strategy Engine        ║"
echo "╚══════════════════════════════════════╝"
echo ""

# --- System setup ---
echo "[1/8] System updates..."
apt update -qq && apt install -y -qq curl git tmux jq python3 python3-pip ufw > /dev/null 2>&1
echo "  ✓ System packages"

# --- Node.js 22 ---
echo "[2/8] Node.js..."
if command -v node &> /dev/null && [[ $(node -v | cut -d. -f1) == v2[2-9]* || $(node -v | cut -d. -f1) == v2[4-9]* ]]; then
    echo "  ✓ Node.js already installed: $(node -v)"
else
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - > /dev/null 2>&1
    apt install -y -qq nodejs > /dev/null 2>&1
    echo "  ✓ Node.js installed: $(node -v)"
fi

# --- OpenClaw (PINNED safe version) ---
echo "[3/8] OpenClaw 2026.3.23-2..."
npm install -g openclaw@2026.3.23-2 > /dev/null 2>&1
echo "  ✓ OpenClaw $(openclaw --version 2>/dev/null || echo 'installed')"

# --- Firewall ---
echo "[4/8] Security hardening..."
ufw allow 22/tcp > /dev/null 2>&1
ufw deny 18789 > /dev/null 2>&1  # Block gateway port from public
ufw --force enable > /dev/null 2>&1 || true
echo "  ✓ Firewall configured (port 18789 blocked)"

# --- Python packages ---
echo "[5/8] Python data packages..."
pip3 install yfinance finnhub-python requests --break-system-packages -q 2>/dev/null || \
pip3 install yfinance finnhub-python requests -q 2>/dev/null
echo "  ✓ yfinance, finnhub, requests"

# --- Directory structure ---
echo "[6/8] Creating workspace..."
BASE="/root/quantai-v2"
mkdir -p $BASE/.openclaw
mkdir -p $BASE/workspace-orchestrator
mkdir -p $BASE/workspace-research
mkdir -p $BASE/workspace-infra
mkdir -p $BASE/workspace-journal
mkdir -p $BASE/shared-data/{journal/paper,journal/real,journal/digests,strategies,cache/sofi_history,scripts,logs}

# Initialize empty journals
touch $BASE/shared-data/journal/paper/trades.jsonl
touch $BASE/shared-data/journal/real/trades.jsonl
echo "  ✓ Directory structure created"

# --- Write all config files ---
echo "[7/8] Writing agent configurations..."

# ---- .env template ----
cat > $BASE/.env << 'EOF'
# === QuantAI v2 — Fill in ALL values below ===

# Anthropic API Key
ANTHROPIC_API_KEY=

# Alpaca Paper Trading
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER=true
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Data Sources
FINNHUB_API_KEY=
ALPHA_VANTAGE_API_KEY=

# Discord Bot Tokens (one per agent)
DISCORD_TOKEN_ORCHESTRATOR=
DISCORD_TOKEN_RESEARCH=
DISCORD_TOKEN_INFRA=
DISCORD_TOKEN_JOURNAL=

# Discord Server + Channel IDs
DISCORD_GUILD_ID=
DISCORD_CHANNEL_CHAT=
DISCORD_CHANNEL_RESEARCH=
DISCORD_CHANNEL_INFRA=
DISCORD_CHANNEL_JOURNAL=
DISCORD_CHANNEL_ALERTS=

# GitHub
GITHUB_PAT=
GITHUB_REPO=amitc3353/QuantAI
EOF

# ---- Orchestrator AGENTS.md ----
cat > $BASE/workspace-orchestrator/AGENTS.md << 'EOF'
# Orchestrator — Operating Manual

You are the Orchestrator for QuantAI, Amit's trading system.
You live in #chat. You are Amit's primary interface.

## Role
- Answer questions about trading, strategy, positions, market conditions
- Route tasks: research→#research, bugs→#infra, trades→#journal
- Concise, actionable, mobile-first responses

## Current strategy: SOFI Collar
- 200 shares at ~$15 (paper)
- SELL $16 calls biweekly → +$110/cycle
- BUY $12 puts monthly → -$50
- Net target: $170/month | Max loss: $600

## Trigger actions (pre-decided)
- $15.70: Monitor
- $16.00: Roll call to $18, 2wk out
- $16+ called away: Accept profit, restart on dip
- $12.50: Monitor, assess thesis
- $12.00: Exercise put or roll to $10

## Rules
- Max 3-4 sentences for simple questions
- Bold key numbers
- Never encourage breaking strategy rules
- If data needed, direct to #research
- Read shared files for current state

## Shared files
- /root/quantai-v2/shared-data/strategies/sofi_collar.json
- /root/quantai-v2/shared-data/cache/sofi_latest.json
- /root/quantai-v2/shared-data/journal/paper/trades.jsonl
EOF

cat > $BASE/workspace-orchestrator/SOUL.md << 'EOF'
# Soul
You are QuantAI Orchestrator — a sharp, direct trading co-pilot.
No fluff. Lead with the answer. Use numbers.
Push back on emotional decisions. Celebrate wins briefly, analyze losses thoroughly.
Never encourage deviation from strategy rules without thesis justification.
EOF

# ---- Research AGENTS.md ----
cat > $BASE/workspace-research/AGENTS.md << 'EOF'
# Research Agent — Operating Manual

You live in #research. Your ONE job: accurate, actionable SOFI intelligence.

## Daily brief format (SHORT for mobile)
```
SOFI Daily — [date]
Price: $XX.XX (▲/▼ X.X%)
Vol: XXM | IV: XX% | IV Rank: XX%
Call ($16): $X.XX away | Put ($12): $X.XX away
RSI: XX | MACD: [signal] | 50DMA: $XX.XX
News: [1 sentence]
Options: $16C bid $X.XX | $12P bid $X.XX
→ Action: [HOLD/SELL CALL/ROLL/MONITOR]
```

## Data fetching
Use Python + yfinance via Bash tool:
```python
import yfinance as yf
sofi = yf.Ticker("SOFI")
hist = sofi.history(period="5d")
chain = sofi.option_chain(sofi.options[0])
```

Or run the pre-built script:
```bash
python3 /root/quantai-v2/shared-data/scripts/fetch_sofi.py
```

## Alert triggers (post to Discord)
- SOFI within $0.50 of $16 or $12
- IV rank > 60% (sell calls) or < 20% (wait)
- Major news: earnings, analyst changes, CEO activity
- Unusual options volume > 3x average

## Rules
- Never fabricate data
- Save briefs to /root/quantai-v2/shared-data/cache/sofi_latest.json
- Under 2000 chars per Discord message
- When in doubt, recommend HOLD
EOF

cat > $BASE/workspace-research/SOUL.md << 'EOF'
# Soul
You are QuantAI Research — a focused SOFI equity analyst.
Data-first, opinion second. Concise — every word costs screen space.
Think like an options seller: volatility is your friend, surprises are your enemy.
EOF

# ---- Infra AGENTS.md ----
cat > $BASE/workspace-infra/AGENTS.md << 'EOF'
# Infra Agent — Operating Manual

You live in #infra. You have FULL system access (Bash, Read, Write, Edit).

## Capabilities
- Read/edit any file on VPS
- Shell commands, git operations, package management
- System monitoring: disk, memory, processes, logs
- OpenClaw gateway management

## Health check format
```
System Health — [time]
Gateway: ✅/❌ | Agents: X/4 online
Disk: XX% | Memory: XX%
Last SOFI fetch: [time] | Last trade: [time]
Errors (24h): X
```

## Without approval: fix typos, clear cache, restart gateway, read logs, git pull, health checks
## Needs approval: new packages, workspace changes, strategy params, .env changes, trading logic

## GitHub: always branch, never push to main, commit format: [agent] description
## Golden rule: when in doubt, DON'T change it. Report and let Amit decide.
EOF

cat > $BASE/workspace-infra/SOUL.md << 'EOF'
# Soul
You are QuantAI Infra — methodical, careful, paranoid about breaking things.
Diagnose first, then act. Log everything. Always branch before changing code.
Reliability is the product. A system Amit can't trust from his phone is worthless.
EOF

# ---- Journal AGENTS.md ----
cat > $BASE/workspace-journal/AGENTS.md << 'EOF'
# Journal Agent — Operating Manual

You live in #journal. You are the system of record for every trade.

## Trade logging (natural language)
"log: sold 2x SOFI $16C Apr 11 for $1.10" → parse and save as JSONL

Confirm: ✅ Logged: SELL 2x SOFI $16C exp 4/11 @ $1.10 | Mode: PAPER | ID: P001

## File locations
- Paper: /root/quantai-v2/shared-data/journal/paper/trades.jsonl
- Real: /root/quantai-v2/shared-data/journal/real/trades.jsonl
- Digests: /root/quantai-v2/shared-data/journal/digests/

## Commands
- "stats" → total trades, win rate, P&L
- "stats paper/real" → filtered
- "open positions" → currently open
- "log: [trade details]" → record new trade
- "close: [ID] [details]" → close existing trade

## Weekly digest (Fridays)
Trades opened/closed, win rate, premium collected/paid, net income, strategy adherence

## Rules
- NEVER modify existing entries (append-only)
- ASK if description is ambiguous
- Paper IDs: P001, P002... Real IDs: R001, R002...
- Timestamps in ET
EOF

cat > $BASE/workspace-journal/SOUL.md << 'EOF'
# Soul
You are QuantAI Journal — precise, organized, reliable.
Confirm everything before recording. Spot patterns humans miss.
Ask before assuming. A wrong entry is worse than a delayed entry.
EOF

# ---- SOFI strategy config ----
cat > $BASE/shared-data/strategies/sofi_collar.json << 'EOF'
{
  "strategy": "collar",
  "version": "1.0",
  "status": "paper",
  "underlying": {
    "symbol": "SOFI",
    "entry_price": 15.00,
    "shares": 200,
    "contracts": 2,
    "capital": 3000
  },
  "collar": {
    "call_strike": 16.00,
    "call_frequency": "biweekly",
    "call_premium_target": 1.10,
    "put_strike": 12.00,
    "put_frequency": "monthly",
    "put_cost_target": 0.50,
    "net_monthly_target": 170
  },
  "risk": {
    "max_loss": 600,
    "max_loss_per_share": 3.00
  },
  "triggers": {
    "15.70": "MONITOR - near call",
    "16.00": "ROLL call to $18 2wk out",
    "16.10": "AT CALL - roll or accept assignment",
    "12.50": "MONITOR - near put, assess thesis",
    "12.00": "PUT FLOOR - exercise or roll to $10"
  }
}
EOF

# ---- SOFI fetch script ----
cat > $BASE/shared-data/scripts/fetch_sofi.py << 'PYEOF'
#!/usr/bin/env python3
"""Fetch SOFI data — used by Research agent and cron jobs."""
import json, os
from datetime import datetime
import yfinance as yf

CACHE = "/root/quantai-v2/shared-data/cache"

def fetch():
    sofi = yf.Ticker("SOFI")
    hist = sofi.history(period="5d")
    if hist.empty:
        return {"error": "No data (market closed?)"}

    p = float(hist.iloc[-1]["Close"])
    prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else p
    chg = p - prev

    # RSI
    h60 = sofi.history(period="60d")
    d = h60["Close"].diff()
    g = d.where(d > 0, 0).rolling(14).mean()
    l = (-d.where(d < 0, 0)).rolling(14).mean()
    rsi = float(100 - 100 / (1 + (g / l).iloc[-1])) if l.iloc[-1] != 0 else 50

    # SMAs
    sma50 = float(h60["Close"].rolling(50).mean().iloc[-1]) if len(h60) >= 50 else None
    h1y = sofi.history(period="1y")
    sma200 = float(h1y["Close"].rolling(200).mean().iloc[-1]) if len(h1y) >= 200 else None

    # Options
    opts = {}
    try:
        exps = sofi.options
        if exps:
            ch = sofi.option_chain(exps[0])
            c16 = ch.calls[ch.calls.strike == 16.0]
            if not c16.empty:
                r = c16.iloc[0]
                opts["call_16"] = {"bid": float(r.get("bid",0)), "ask": float(r.get("ask",0)),
                                   "iv": float(r.get("impliedVolatility",0)), "oi": int(r.get("openInterest",0) or 0),
                                   "expiry": exps[0]}
            p12 = ch.puts[ch.puts.strike == 12.0]
            if not p12.empty:
                r = p12.iloc[0]
                opts["put_12"] = {"bid": float(r.get("bid",0)), "ask": float(r.get("ask",0)),
                                  "iv": float(r.get("impliedVolatility",0)), "oi": int(r.get("openInterest",0) or 0),
                                  "expiry": exps[0]}
            atm = ch.calls.iloc[(ch.calls.strike - p).abs().argsort()[:1]]
            if not atm.empty:
                opts["atm_iv"] = float(atm.iloc[0].get("impliedVolatility",0))
    except Exception as e:
        opts["error"] = str(e)

    info = sofi.info or {}
    result = {
        "ts": datetime.now().isoformat(), "symbol": "SOFI",
        "price": round(p,2), "change": round(chg,2), "change_pct": round(chg/prev*100,2),
        "volume": int(hist.iloc[-1]["Volume"]), "avg_vol": int(info.get("averageVolume",0)),
        "sma50": round(sma50,2) if sma50 else None,
        "sma200": round(sma200,2) if sma200 else None,
        "rsi14": round(rsi,1),
        "to_call": round(16-p,2), "to_put": round(p-12,2),
        "options": opts
    }

    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(f"{CACHE}/sofi_history", exist_ok=True)
    with open(f"{CACHE}/sofi_latest.json","w") as f: json.dump(result, f, indent=2)
    with open(f"{CACHE}/sofi_history/{datetime.now():%Y-%m-%d}.json","w") as f: json.dump(result, f, indent=2)
    return result

if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2))
PYEOF
chmod +x $BASE/shared-data/scripts/fetch_sofi.py

echo "  ✓ All agent workspaces configured"

# --- Final instructions ---
echo ""
echo "[8/8] Setup complete!"
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  NEXT STEPS (do these now):                             ║"
echo "║                                                         ║"
echo "║  1. Create Discord server + 4 bots:                     ║"
echo "║     https://discord.com/developers/applications         ║"
echo "║     → New Application × 4 (Orchestrator, Research,      ║"
echo "║       Infra, Journal)                                   ║"
echo "║     → Bot tab → Reset Token → COPY IT                  ║"
echo "║     → Enable ALL 3 Privileged Gateway Intents           ║"
echo "║     → OAuth2 → bot scope → Send/Read permissions        ║"
echo "║     → Invite each to your server                        ║"
echo "║                                                         ║"
echo "║  2. Fill in .env:                                       ║"
echo "║     nano /root/quantai-v2/.env                          ║"
echo "║                                                         ║"
echo "║  3. Run onboard:                                        ║"
echo "║     cd /root/quantai-v2                                 ║"
echo "║     source .env                                         ║"
echo "║     openclaw onboard                                    ║"
echo "║                                                         ║"
echo "║  4. Start gateway in tmux:                              ║"
echo "║     tmux new -s quantai                                 ║"
echo "║     cd /root/quantai-v2 && source .env                  ║"
echo "║     openclaw gateway                                    ║"
echo "║     (Ctrl+B, D to detach)                               ║"
echo "║                                                         ║"
echo "║  5. Test from your phone:                               ║"
echo "║     #chat → \"hello\"                                     ║"
echo "║     #research → \"pull SOFI data\"                        ║"
echo "║     #journal → \"show trades\"                            ║"
echo "║     #infra → \"health check\"                             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Workspace: /root/quantai-v2"
echo "OpenClaw version: $(openclaw --version 2>/dev/null || echo 'check with: openclaw --version')"
echo ""
