# Discord Setup Guide — QuantAI v2

Do these steps from your laptop or phone browser. Takes ~15 minutes.

---

## Step 1: Create the Discord Server

1. Open Discord → click "+" on the left sidebar → "Create My Own" → "For me and my friends"
2. Name it: **QuantAI**
3. Create these text channels (right-click "Text Channels" → Create Channel):
   - `#chat` — your main conversation channel (Orchestrator agent lives here)
   - `#research` — daily SOFI analysis, market briefs (Research agent)
   - `#infra` — system health, deploys, error reports (Infra agent)
   - `#journal` — trade logs, paper vs real, weekly digests (Journal agent)
   - `#alerts` — price alerts, position warnings, urgent notifications (all agents can post)
4. Note your **Server ID**: Right-click server name → Copy Server ID
   (Enable Developer Mode first: User Settings → Advanced → Developer Mode)
5. Note each **Channel ID**: Right-click channel → Copy Channel ID

---

## Step 2: Create 4 Bot Applications

Go to https://discord.com/developers/applications

### Bot 1: Orchestrator
1. Click "New Application" → name it "QuantAI Orchestrator"
2. Go to "Bot" tab → click "Reset Token" → **copy and save the token**
3. Under "Privileged Gateway Intents", enable ALL THREE:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent ← CRITICAL, without this the bot can't read messages
4. Go to "OAuth2" → "URL Generator"
   - Scopes: ✅ bot
   - Bot Permissions: ✅ Send Messages, ✅ Read Message History, ✅ Read Messages/View Channels, ✅ Embed Links, ✅ Attach Files, ✅ Add Reactions
5. Copy the generated URL → open it → add bot to your QuantAI server

### Bot 2: Research Agent
Repeat the same steps. Name: "QuantAI Research"

### Bot 3: Infra Agent
Repeat. Name: "QuantAI Infra"

### Bot 4: Journal Agent
Repeat. Name: "QuantAI Journal"

---

## Step 3: Record Your IDs

Fill these in and paste them into your .env file:

```
Server ID:               _______________
#chat channel ID:        _______________
#research channel ID:    _______________
#infra channel ID:       _______________
#journal channel ID:     _______________
#alerts channel ID:      _______________

Orchestrator bot token:  _______________
Research bot token:      _______________
Infra bot token:         _______________
Journal bot token:       _______________
```

---

## Step 4: Channel Permissions

For each channel, set permissions so only the right bot can post:

### #chat
- Orchestrator bot: Read + Write
- All other bots: Read only (they can see context but Orchestrator responds)

### #research
- Research bot: Read + Write
- Orchestrator: Read only
- Others: Read only

### #infra
- Infra bot: Read + Write
- Orchestrator: Read only
- Others: Read only

### #journal
- Journal bot: Read + Write
- Orchestrator: Read only
- Others: Read only

### #alerts
- ALL bots: Read + Write (any agent can post urgent alerts)

---

## Step 5: Verify

After OpenClaw is configured (next step), test each bot:
1. Go to #chat → type "hello" → Orchestrator should respond
2. Go to #research → type "SOFI analysis" → Research agent should respond
3. Go to #infra → type "health check" → Infra agent should respond
4. Go to #journal → type "show trades" → Journal agent should respond

If a bot doesn't respond:
- Check the bot token in .env
- Make sure Message Content Intent is enabled
- Make sure the bot was actually invited to the server
- Check OpenClaw logs: `openclaw gateway --log-level debug`
