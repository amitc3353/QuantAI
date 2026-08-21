# Claude Auto-Trader — Complete Setup Guide

## Overview

This guide walks you through every click and command to get the trading system live.
Total time: ~45-60 minutes.

```
Step 0: GitHub private repo (5 min)
Step 1: Discord server + bot (10 min)
Step 2: API keys — Alpaca, Anthropic, Alpha Vantage (10 min)
Step 3: Wire up channel IDs (5 min)
Step 4: Hetzner VPS (15 min)
Step 5: Deploy + test (10 min)
```

---

## Step 0: GitHub Private Repo + Local Setup

You'll work locally with Claude CLI in VSCode, and deploy to VPS from the repo.

### 0.1 — Create the repo on GitHub

1. Go to https://github.com/new
2. Fill in:
   - **Repository name**: `claude-auto-trader`
   - **Description**: `Claude-powered autonomous trading system`
   - **Visibility**: **Private**
   - Do NOT add README, .gitignore, or license (we already have them)
3. Click **Create repository**
4. Copy the SSH URL: `git@github.com:YOUR_USERNAME/claude-auto-trader.git`

### 0.2 — Push the scaffold to GitHub

Extract the tarball you downloaded and push it:

```bash
# Extract the project
tar xzf claude-auto-trader.tar.gz
cd claude-auto-trader

# Initialize git and push
git init
git add .
git commit -m "Phase 1: scaffold — Discord bot, guard engine, orchestrator, Docker stack"
git branch -M main
git remote add origin git@github.com:YOUR_USERNAME/claude-auto-trader.git
git push -u origin main
```

### 0.3 — Open in VSCode with Claude CLI

```bash
# Open the project
code claude-auto-trader/

# If you use Claude Code CLI, start it in the project root
cd claude-auto-trader
claude
```

Now you can make changes locally, commit, push, and deploy to VPS.

### 0.4 — Add a CLAUDE.md for Claude CLI context

Create this file in your project root so Claude CLI understands the codebase:

```bash
# This gets created in the next section of the tarball,
# but you can also create it manually — see the CLAUDE.md
# file included in the project.
```

---

## Step 1: Discord Server + Bot

### 1.1 — Create the Discord server

1. Open Discord (app or browser)
2. Click the **+** button in the left sidebar
3. Select **Create My Own** → **For me and my friends**
4. Name it: `Auto-Trader` (or whatever you like)
5. Click **Create**

### 1.2 — Create the channels

In your new server, create these text channels.
Right-click the **Text Channels** category → **Create Channel** for each:

```
#command           — Where you type commands
#research          — Morning briefs land here
#trade-proposals   — Trade cards with ✅/❌ approval
#guard-log         — Guard approvals and rejections
#execution-log     — Filled orders and confirmations
#system-health     — Heartbeat, startup, errors
#pr-updates        — Self-improvement PRs (Phase 3)
```

**Tip**: You can also create a category called "Trading System" and put all 7 channels under it.

### 1.3 — Enable Developer Mode (needed for channel IDs)

1. Go to **User Settings** (gear icon, bottom left)
2. Navigate to **App Settings** → **Advanced**
3. Toggle **Developer Mode** ON
4. Close settings

Now you can right-click any channel → **Copy Channel ID**.

### 1.4 — Create the Discord bot

1. Go to https://discord.com/developers/applications
2. Click **New Application**
3. Name it: `TraderBot` → click **Create**
4. You'll land on the **General Information** page

### 1.5 — Configure the bot

**Bot tab:**
1. Click **Bot** in the left sidebar
2. Click **Reset Token** → **Yes, do it!**
3. **COPY THE TOKEN NOW** — you won't see it again
   - Save it somewhere safe temporarily (you'll put it in `.env` soon)
4. Scroll down to **Privileged Gateway Intents**:
   - Toggle ON: **Message Content Intent**
   - Toggle ON: **Server Members Intent** (optional but useful)
5. Click **Save Changes**

**OAuth2 tab — Generate invite link:**
1. Click **OAuth2** in the left sidebar
2. Under **OAuth2 URL Generator**:
   - In **Scopes**, check: `bot`, `applications.commands`
   - In **Bot Permissions**, check:
     - Send Messages
     - Send Messages in Threads
     - Embed Links
     - Attach Files
     - Read Message History
     - Add Reactions
     - Use Slash Commands
3. Copy the **Generated URL** at the bottom
4. Open that URL in your browser
5. Select your **Auto-Trader** server → **Authorize**

The bot should now appear in your server (offline — it's not running yet).

### 1.6 — Get your Guild (Server) ID

1. Right-click your server name at the top of the channel list
2. Click **Copy Server ID**
3. Save this — it goes in `.env` as `DISCORD_GUILD_ID`

---

## Step 2: API Keys

### 2.1 — Alpaca (Paper Trading)

1. Go to https://alpaca.markets
2. Click **Sign Up** (free account)
3. After signup, go to the **Paper Trading** dashboard
4. Navigate to **API Keys** (usually in the sidebar or account settings)
5. Click **Generate New Key**
6. Save both:
   - **API Key ID** → goes in `ALPACA_API_KEY`
   - **Secret Key** → goes in `ALPACA_SECRET_KEY`

**Important**: Make sure you're generating **paper trading** keys,
not live keys. The URL should show `paper-api.alpaca.markets`.

### 2.2 — Anthropic (Claude API)

1. Go to https://console.anthropic.com
2. Sign in or create an account
3. Navigate to **API Keys** (Settings → API Keys)
4. Click **Create Key**
5. Name it: `auto-trader`
6. Copy the key → goes in `ANTHROPIC_API_KEY`

**Billing**: Add a payment method if you haven't already.
Phase 1 usage will be very light — roughly $8-15/month.

### 2.3 — Alpha Vantage (Market Data)

1. Go to https://www.alphavantage.co/support/#api-key
2. Fill in the form (name, email, etc.)
3. Click **Get Free API Key**
4. Copy the key → goes in `ALPHA_VANTAGE_API_KEY`

Free tier: 25 requests/day — enough for a 10-symbol watchlist.

### 2.4 — Fill in your .env file

In your project root, copy the example and fill in your keys:

```bash
cp .env.example .env
```

Then edit `.env` with your actual values:

```env
# Discord
DISCORD_BOT_TOKEN=MTIzNDU2Nzg5... (your bot token from Step 1.5)
DISCORD_GUILD_ID=1234567890123456 (your server ID from Step 1.6)

# Anthropic
ANTHROPIC_API_KEY=sk-ant-... (your key from Step 2.2)

# Alpaca Paper
ALPACA_API_KEY=PK... (your paper key from Step 2.1)
ALPACA_SECRET_KEY=... (your paper secret from Step 2.1)
ALPACA_PAPER=true

# Alpha Vantage
ALPHA_VANTAGE_API_KEY=... (your key from Step 2.3)

# Leave the rest as defaults for now
TZ=America/New_York
LOG_LEVEL=INFO
TRADING_MODE=paper
```

---

## Step 3: Wire Up Discord Channel IDs

Now that your channels exist and Developer Mode is on:

1. In Discord, right-click each channel → **Copy Channel ID**
2. Edit `configs/channels.json` with the real IDs:

```json
{
  "command": 1234567890123456,
  "research": 1234567890123457,
  "trade_proposals": 1234567890123458,
  "guard_log": 1234567890123459,
  "execution_log": 1234567890123460,
  "system_health": 1234567890123461,
  "pr_updates": 1234567890123462
}
```

3. Also add the channel IDs to your `.env` file as backup:

```env
CHANNEL_COMMAND=1234567890123456
CHANNEL_RESEARCH=1234567890123457
CHANNEL_TRADE_PROPOSALS=1234567890123458
CHANNEL_GUARD_LOG=1234567890123459
CHANNEL_EXECUTION_LOG=1234567890123460
CHANNEL_SYSTEM_HEALTH=1234567890123461
CHANNEL_PR_UPDATES=1234567890123462
```

4. Commit and push:

```bash
git add configs/channels.json
git commit -m "Add Discord channel IDs"
git push
```

**Do NOT commit .env** — it's in .gitignore for a reason.

---

## Step 4: Hetzner VPS

### 4.1 — Create the server

1. Go to https://console.hetzner.cloud
2. Sign up / log in
3. Click **+ Create Server**
4. Configure:
   - **Location**: Ashburn, VA (closest to NYSE/Alpaca)
   - **Image**: Ubuntu 24.04
   - **Type**: Shared vCPU → **CX31** (2 vCPU, 4GB RAM, €7.49/mo)
     - CX41 (4 vCPU, 8GB) if you want headroom for Phase 2
   - **SSH Key**: Add your public key
     - If you don't have one: `ssh-keygen -t ed25519` on your machine
     - Paste the contents of `~/.ssh/id_ed25519.pub`
   - **Name**: `auto-trader`
5. Click **Create & Buy Now**
6. Note the IP address when it's ready

### 4.2 — Run the VPS setup script

From your local machine:

```bash
# First-time hardening (run as root)
ssh root@YOUR_VPS_IP 'bash -s' < scripts/setup-vps.sh
```

This does:
- System updates
- Creates `trader` user (non-root)
- Installs Docker + Docker Compose
- Configures UFW firewall (SSH only)
- Installs fail2ban
- Hardens SSH (disables root login + password auth)

**After this, root SSH is disabled.** Use `trader@YOUR_VPS_IP` from now on.

### 4.3 — Clone the repo on VPS

SSH into the VPS as the trader user:

```bash
ssh trader@YOUR_VPS_IP
```

Set up GitHub SSH access on the VPS:

```bash
# Generate SSH key on VPS
ssh-keygen -t ed25519 -C "auto-trader-vps"

# Print the public key
cat ~/.ssh/id_ed25519.pub
```

Copy that public key and add it as a **Deploy Key** in GitHub:
1. Go to your repo → **Settings** → **Deploy keys**
2. Click **Add deploy key**
3. Title: `auto-trader-vps`
4. Paste the public key
5. Check **Allow write access** (needed for self-improvement PRs later)
6. Click **Add key**

Now clone on the VPS:

```bash
cd ~
git clone git@github.com:YOUR_USERNAME/claude-auto-trader.git
cd claude-auto-trader
```

### 4.4 — Configure .env on VPS

```bash
cp .env.example .env
nano .env
# Fill in ALL the same values from Step 2.4
# Save: Ctrl+O, Enter, Ctrl+X
```

**Security check**: Verify .env permissions:
```bash
chmod 600 .env
```

---

## Step 5: Deploy + Test

### 5.1 — Build and start

On the VPS:

```bash
cd ~/claude-auto-trader
docker compose build
docker compose up -d
```

Watch the logs:

```bash
docker compose logs -f
```

You should see:
```
trader-discord    | ✅ Bot connected as TraderBot#1234
trader-discord    | Synced 7 slash commands
trader-guards     | INFO: Uvicorn running on http://0.0.0.0:8100
trader-orchestrator | Orchestrator starting...
trader-orchestrator | Scheduler started with 3 jobs
```

### 5.2 — Test in Discord

**Test 1: Bot is alive**
In your `#command` channel, type:
```
/status
```
You should see an embed showing system status, trading mode (paper), and guard engine status.

**Test 2: Guard engine works**
```
/guard_check symbol:SPY position_pct:3.0 max_loss_pct:1.5 dte:30
```
Expected: ✅ APPROVE (assuming it's during market hours and not in a no-trade zone)

```
/guard_check symbol:PLTR position_pct:3.0 max_loss_pct:1.5 dte:30
```
Expected: 🛡️ REJECT — PLTR is not on the whitelist

```
/guard_check symbol:SPY position_pct:8.0 max_loss_pct:1.5 dte:30
```
Expected: 🛡️ REJECT — position size exceeds 5% limit

**Test 3: Emergency stop**
```
/emergency_stop
```
Check #system-health for the halt notification.
Then:
```
/resume
```

**Test 4: Watchlist**
```
/watchlist
/watchlist action:add symbol:TSLA
/watchlist action:remove symbol:TSLA
```

**Test 5: View rules**
```
/rules
```
Should display the guard config JSON.

### 5.3 — Verify health from command line

On the VPS:
```bash
# Guard engine health
curl http://localhost:8100/health

# Guard engine config
curl http://localhost:8100/config

# Test a guard check directly
curl -X POST http://localhost:8100/check \
  -H "Content-Type: application/json" \
  -d '{"symbol":"SPY","position_pct":3.0,"max_loss_pct":1.5,"dte":30}'
```

### 5.4 — Set up daily backups (optional but recommended)

On the VPS:
```bash
# Make backup script executable
chmod +x scripts/backup.sh

# Add to crontab — runs at 5 AM daily
crontab -e
# Add this line:
0 5 * * * /home/trader/claude-auto-trader/scripts/backup.sh
```

---

## Deploying Updates

After making changes locally with Claude CLI:

```bash
# Local: commit and push
git add .
git commit -m "your change description"
git push

# Then deploy to VPS (from local machine)
./scripts/deploy.sh YOUR_VPS_IP

# Or manually on VPS
ssh trader@YOUR_VPS_IP
cd ~/claude-auto-trader
git pull
docker compose build
docker compose up -d
```

---

## Troubleshooting

**Bot doesn't come online in Discord:**
- Check token: `docker compose logs discord-bot`
- Verify `DISCORD_BOT_TOKEN` in `.env` is correct
- Make sure Message Content Intent is enabled in Discord Developer Portal

**Slash commands don't appear:**
- They can take up to 1 hour to propagate for global commands
- For instant sync, make sure `DISCORD_GUILD_ID` is set (guild commands sync immediately)
- Try: kick the bot and re-invite it

**Guard engine unreachable:**
- Check: `docker compose ps` — is `trader-guards` running?
- Check: `docker compose logs guard-engine`
- Test directly: `curl http://localhost:8100/health` on VPS

**Container keeps restarting:**
- Check: `docker compose logs <service-name>`
- Common cause: missing env vars in `.env`

---

## What's Next: Phase 2

Once everything in this guide is working:

1. Connect Alpaca MCP for actual paper order placement
2. Install TradingAgents for automated research briefs
3. Add py_vollib for local Greeks computation
4. Wire up the full Research → Analysis → Guard → Execution pipeline
5. Morning briefs start flowing to #research automatically

Tell Claude: "Let's start Phase 2 — wire up Alpaca MCP for paper trading."
