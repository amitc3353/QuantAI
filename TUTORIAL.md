# QuantAI — System Tutorial

## How to use your autonomous trading system, every day.

---

## Your Discord Server — The Command Center

Everything happens in Discord. Here's what each channel is for:

| Channel | What happens here | Who posts |
|---|---|---|
| **#command** | Slash commands — trading, analysis, system ops | You |
| **#chat** | Talk naturally — ask questions, discuss strategy, brainstorm | You + AI |
| **#research** | Morning briefs land here at 6:30 AM ET every trading day | Orchestrator (auto) |
| **#trade-proposals** | Trade cards with analysis. React ✅ to approve, ❌ to reject | Orchestrator + You |
| **#guard-log** | Guard approvals and rejections | Guard Engine (auto) |
| **#execution-log** | Filled orders, confirmations | Execution Agent (auto) |
| **#system-health** | Startup messages, EOD scoring, errors, weekly reviews | System (auto) |
| **#pr-updates** | Self-improvement PRs when score < 90 | Self-Improve Engine (auto) |

---

## Daily Workflow — What Happens When

### 6:30 AM ET (Automated — you're probably sleeping)
The orchestrator wakes up and:
1. Fetches real market data for your watchlist (SPY, QQQ, NVDA, etc.)
2. Sends data to Claude for analysis
3. Posts a morning brief to **#research** with bias, conviction, risks for each symbol
4. If any symbol hits conviction ≥ 7, auto-generates a trade proposal in **#trade-proposals**

**What you do:** Check #research when you wake up. Read the brief. See if any proposals landed.

### 9:30 AM - 4:00 PM ET (Market Hours)
This is when you trade, analyze, and learn.

**What you do:**
- Review any auto-proposals in #trade-proposals
- Use `/greeks`, `/bull_put`, `/iron_condor` to analyze trades yourself
- Use `/buy` or `/sell` to execute (bot checks guards, waits for your ✅)
- Ask questions in #chat: "Why is SPY down today?" or "Should I close my position?"

### 4:30 PM ET (Automated)
EOD scoring runs:
1. Scores the day's trades 0-100
2. Extracts lessons and saves them to memory
3. If score < 90, generates an improvement PR on GitHub
4. Posts results to #system-health

**What you do:** Review the score in #system-health. Read the lessons. Check GitHub for any auto-PRs.

### 4:45 PM ET Friday (Automated)
Weekly review:
1. Aggregates the entire week's performance
2. Identifies patterns across all trades
3. Proposes strategic adjustments
4. Posts to #system-health

**What you do:** Read the weekly review. Discuss in #chat if you have questions.

---

## Commands Reference — What to Type and When

### Trading Commands (use in #command or #trade-proposals)

| Command | When to use | Example |
|---|---|---|
| `/account` | Check your portfolio value, cash, buying power | `/account` |
| `/positions` | See all open positions with P&L | `/positions` |
| `/orders` | See open or recent orders | `/orders status:open` |
| `/quote SPY` | Get live price, bid/ask, volume | `/quote symbol:SPY` |
| `/buy` | Buy shares (goes through guards, needs ✅) | `/buy symbol:SPY qty:1` |
| `/sell` | Sell shares (goes through guards, needs ✅) | `/sell symbol:SPY qty:1` |
| `/limit_buy` | Buy at specific price | `/limit_buy symbol:SPY qty:1 price:665.00` |
| `/limit_sell` | Sell at specific price | `/limit_sell symbol:SPY qty:1 price:675.00` |
| `/close` | Close an entire position | `/close symbol:SPY` |
| `/cancel` | Cancel an open order | `/cancel order_id:abc123` |

### Options Analysis Commands

| Command | When to use | Example |
|---|---|---|
| `/greeks` | Compute delta/theta/gamma/vega for any option | `/greeks symbol:SPY strike:665 dte:30 option_type:put iv:0.20` |
| `/bull_put` | Analyze a bull put spread with full P&L | `/bull_put symbol:SPY short_strike:660 long_strike:655 dte:30 iv:0.22` |
| `/iron_condor` | Analyze an iron condor | `/iron_condor symbol:SPY put_short:655 put_long:650 call_short:685 call_long:690 dte:30 iv:0.20` |
| `/covered_call` | Analyze a covered call | `/covered_call symbol:SPY strike:680 dte:30 iv:0.18` |

### Guard & Rules Commands

| Command | When to use | Example |
|---|---|---|
| `/guard_check` | Test if a trade would pass guards | `/guard_check symbol:SPY position_pct:3 max_loss_pct:1.5 dte:30` |
| `/rules` | View current guard rules | `/rules` |
| `/emergency_stop` | HALT all new trades immediately | `/emergency_stop` |
| `/resume` | Resume trading after halt | `/resume` |
| `/watchlist` | View or update your watchlist | `/watchlist action:add symbol:TSLA` |
| `/status` | Quick system status | `/status` |

### Dev & Ops Commands (Infra Agent)

| Command | When to use | Example |
|---|---|---|
| `/health` | Full system health — containers, memory, disk, git | `/health` |
| `/logs` | View service logs | `/logs service:guard-engine lines:50` |
| `/read` | Read any project file | `/read filepath:configs/guard_config.json` |
| `/ls` | List directory contents | `/ls path:discord-bot/cogs` |
| `/edit` | Edit a file (needs ✅ approval) | `/edit filepath:configs/watchlist.json find:TSLA replace:AMD` |
| `/git` | Git operations | `/git action:status` or `/git action:log arg:5` |
| `/commit` | Commit + push changes (needs ✅) | `/commit message:update watchlist` |
| `/deploy` | Pull latest code from GitHub | `/deploy` |
| `/restart` | Restart a service (needs ✅) | `/restart service:guard-engine` |
| `/run` | Run any shell command | `/run cmd:uptime` |
| `/config` | View/edit config files | `/config file:guards action:view` |

### Memory & Learning Commands

| Command | When to use | Example |
|---|---|---|
| `remember: [lesson]` | Save a lesson to memory (type in #chat) | `remember: Iron condors work best when VIX is 18-25` |
| `lessons` | View saved lessons (type in #chat) | `lessons` or `lessons iron condor` |
| `stats` | View trade statistics (type in #chat) | `stats` |

---

## The #chat Channel — Your AI Co-Founder

This is where you have natural conversations. No slash commands needed — just type.

### Learning & Education
```
What is an iron condor in simple terms?
Explain delta to me like I'm 5
Why do we sell at delta 0.10?
What happens to our position if SPY drops 2% today?
Walk me through how theta decay works on a 0DTE option
```

### Strategy Discussion
```
Should we trade iron condors on FOMC days?
What's a better strike selection — delta 0.08 or 0.15?
Compare bull put spreads vs iron condors for our account size
I'm seeing VIX at 28 today — should we be more aggressive or conservative?
```

### Trade Review
```
How did our trades do this week?
Show me our win rate on morning entries vs afternoon entries
What patterns do you see in our losing trades?
What should we change about our strategy based on this week's data?
```

### System Questions
```
What guard rules are currently active?
Why did the guard reject that SPY trade yesterday?
What's our current portfolio delta?
How much buying power do we have left?
```

The chat agent has memory — it remembers every conversation, every trade, every lesson. The more you talk to it, the smarter it gets about YOUR trading patterns.

---

## The Guard Engine — Rules That Never Bend

Every trade must pass ALL guards. No exceptions. Here's what gets checked:

### Position Rules
- **Max 5% of portfolio** per single trade
- **Max 2% loss** per trade (defined at entry)
- **Max 10 contracts** per position
- **Min 21 days** to expiry (except covered calls)
- **Min 100 open interest** on the option
- **Max $0.15 bid-ask spread**

### Portfolio Rules
- **Max ±0.30 portfolio delta** (prevents directional overexposure)
- **Max -$50/day portfolio theta** (prevents excessive decay risk)
- **Max 8 simultaneous positions**
- **Max 3 positions in same sector**
- **-3% daily loss triggers auto-pause**

### Timing Rules
- **No trading 9:30-9:45 AM** (open volatility)
- **No trading 3:45-4:00 PM** (close volatility)
- **48-hour earnings blackout** (before AND after)
- **VIX > 35 = advisory only** (no auto-execution)
- **24-hour cooldown** after closing a symbol

### Changing Rules
You can tighten rules anytime. To loosen a rule:
1. Discuss in #chat why you want to change it
2. Use `/config guards edit [key] [value]` to update
3. The change runs on paper for 2 weeks before going live

---

## Automated Systems — What Runs Without You

| System | When | What it does |
|---|---|---|
| Morning Brief | 6:30 AM ET Mon-Fri | Market analysis → #research |
| Auto-Proposals | 6:30 AM ET Mon-Fri | High-conviction trades → #trade-proposals |
| Health Checks | Every 5 min, market hours | Verifies all services healthy |
| EOD Scoring | 4:30 PM ET Mon-Fri | Score trades, extract lessons, auto-PR if < 90 |
| Weekly Review | Friday 4:45 PM ET | Week summary, patterns, recommendations |
| Self-Improvement | After EOD if score < 90 | Creates GitHub PR with suggested fixes |

---

## Development Workflow — Making Changes

### Small changes (config edits, watchlist updates):
Do it from Discord:
```
/config guards edit position.max_position_pct 3.0
/watchlist action:add symbol:AMZN
/restart guard-engine
```

### Code changes (new features, bug fixes):
Do it locally with Claude CLI:
```bash
cd ~/QuantAI
# Make changes with Claude CLI or VSCode
git add . && git commit -m "your change" && git push
deploy-trader
```

### Emergency:
```
/emergency_stop     — halts all new trades instantly
/restart all        — restarts all services
deploy-trader       — full rebuild from your Mac
```

---

## Memory System — How the AI Learns

The system has persistent memory across sessions:

### What gets remembered automatically:
- Every trade (entry, exit, P&L, reasoning, Greeks at entry)
- Every conversation in #chat
- Every decision (rule changes, strategy adjustments)
- Every lesson from EOD scoring
- Every system event (deploys, errors, restarts)

### What you should manually save:
When you learn something important, tell the bot:
```
remember: Never trade iron condors on FOMC announcement days — the VIX spike kills both sides
remember: Our best trades happen when we enter at 10:30 AM instead of 9:50 AM
remember: $5 wide spreads on SPY give better risk/reward than $3 wide
```

### How memory improves trading:
Before every trade, the bot loads relevant memory:
- "Last time VIX was this high, our iron condors lost money"
- "This strike delta matches our best-performing trades"
- "We learned to skip Mondays after long weekends"

The more data it has, the better its decisions get.

---

## Paper vs Live Trading

Right now everything runs in **paper mode**. Paper trades use fake money through Alpaca's simulator. The experience is identical to live trading except no real money is at risk.

### Current mode: PAPER
- $10k simulated account
- All trades are practice
- Full logging and scoring
- Perfect for learning

### Switching to live (future):
1. Prove consistent results on paper (2+ months, 5%+ monthly)
2. Change `TRADING_MODE=live` in .env
3. Add live Alpaca API keys
4. Start with small allocation ($5k)
5. All guard rules still apply — guards don't care about paper vs live

---

## Quick Start for Monday

1. Wake up, check **#research** for the morning brief
2. Review any auto-proposals in **#trade-proposals**
3. In **#command**, run `/account` to see your paper balance
4. Run `/quote SPY` to see the current price
5. Try an analysis: `/iron_condor symbol:SPY put_short:660 put_long:655 call_short:685 call_long:690 dte:0 iv:0.20`
6. If you like the setup, use `/buy` or the autonomous bot (once built) to execute
7. Monitor positions with `/positions` throughout the day
8. At EOD, check **#system-health** for the daily score
9. Ask questions in **#chat** about anything you don't understand

---

## Key Principles

1. **Never override the guards.** They exist to protect you from yourself.
2. **Paper first.** Every new strategy runs on paper for 2+ weeks minimum.
3. **Journal everything.** The system does this automatically — your job is to READ the journals.
4. **Ask questions.** The #chat agent is there to teach you. No question is too basic.
5. **One strategy at a time.** Master SPY iron condors before adding anything else.
6. **The system gets smarter every day.** But only if you engage with it — review trades, save lessons, discuss in chat.
