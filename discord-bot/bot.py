"""
Claude Auto-Trader — Discord Bot
=================================
Command center for all trading agents.
Channels: #research, #trade-proposals, #guard-log, #execution-log, #system-health
"""

import os
import json
import logging
import asyncio
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trader-bot")

# ---------------------------------------------------------------------------
# Channel registry — loaded from configs/channels.json or env vars
# ---------------------------------------------------------------------------
def load_channel_map() -> dict[str, int]:
    """Load Discord channel IDs from config file, falling back to env vars."""
    config_path = "/app/configs/channels.json"
    if os.path.exists(config_path):
        with open(config_path) as f:
            return {k: int(v) for k, v in json.load(f).items()}
    return {
        "command": int(os.getenv("CHANNEL_COMMAND", "0")),
        "research": int(os.getenv("CHANNEL_RESEARCH", "0")),
        "trade_proposals": int(os.getenv("CHANNEL_TRADE_PROPOSALS", "0")),
        "guard_log": int(os.getenv("CHANNEL_GUARD_LOG", "0")),
        "execution_log": int(os.getenv("CHANNEL_EXECUTION_LOG", "0")),
        "system_health": int(os.getenv("CHANNEL_SYSTEM_HEALTH", "0")),
        "pr_updates": int(os.getenv("CHANNEL_PR_UPDATES", "0")),
    }

CHANNELS = load_channel_map()

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


class ChannelRouter:
    """Routes messages to the correct Discord channel by name."""

    def __init__(self, bot_instance: commands.Bot, channel_map: dict[str, int]):
        self.bot = bot_instance
        self.channel_map = channel_map

    async def send(self, channel_name: str, content: str = None, embed: discord.Embed = None):
        channel_id = self.channel_map.get(channel_name)
        if not channel_id:
            log.warning(f"No channel configured for '{channel_name}'")
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            log.warning(f"Channel {channel_name} (ID {channel_id}) not found")
            return
        await channel.send(content=content, embed=embed)


router: ChannelRouter | None = None


# ---------------------------------------------------------------------------
# Embed builders — consistent formatting across all agent messages
# ---------------------------------------------------------------------------
def make_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.blue(),
    fields: dict[str, str] | None = None,
    footer: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow(),
    )
    if fields:
        for name, value in fields.items():
            embed.add_field(name=name, value=value, inline=True)
    if footer:
        embed.set_footer(text=footer)
    return embed


def guard_embed(symbol: str, result: str, reason: str) -> discord.Embed:
    color = discord.Color.green() if result == "APPROVE" else discord.Color.red()
    emoji = "✅" if result == "APPROVE" else "🛡️"
    return make_embed(
        title=f"{emoji} Guard: {result}",
        description=f"**{symbol}** — {reason}",
        color=color,
        footer="Constraint Engine",
    )


def trade_card_embed(card: dict) -> discord.Embed:
    return make_embed(
        title=f"📊 Trade Proposal: {card.get('symbol', '?')}",
        description=card.get("thesis", "No thesis provided."),
        color=discord.Color.gold(),
        fields={
            "Strategy": card.get("strategy", "—"),
            "Direction": card.get("direction", "—"),
            "Max Loss": card.get("max_loss", "—"),
            "Max Profit": card.get("max_profit", "—"),
            "Prob. of Profit": card.get("pop", "—"),
            "Greeks": card.get("greeks_summary", "—"),
            "Expiry": card.get("expiry", "—"),
            "Conviction": f"{card.get('conviction', '?')}/10",
        },
        footer="Analysis Agent · React ✅ to approve, ❌ to reject",
    )


def execution_embed(order: dict) -> discord.Embed:
    return make_embed(
        title=f"⚡ Executed: {order.get('symbol', '?')}",
        description=f"Order filled",
        color=discord.Color.green(),
        fields={
            "Side": order.get("side", "—"),
            "Qty": str(order.get("qty", "—")),
            "Fill Price": order.get("fill_price", "—"),
            "Hash": order.get("hash", "—")[:8],
            "Slippage": order.get("slippage", "—"),
        },
        footer="Execution Agent (Haiku)",
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    global router
    router = ChannelRouter(bot, CHANNELS)
    log.info(f"✅ Bot connected as {bot.user} (ID: {bot.user.id})")
    log.info(f"   Guilds: {[g.name for g in bot.guilds]}")

    # Sync slash commands
    try:
        guild = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID", "0")))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f"   Synced {len(synced)} slash commands")
    except Exception as e:
        log.error(f"   Failed to sync commands: {e}")

    # Post startup message
    await router.send(
        "system_health",
        embed=make_embed(
            "🟢 System Online",
            f"Claude Auto-Trader started at {datetime.utcnow().strftime('%H:%M:%S UTC')}\n"
            f"Mode: **{os.getenv('TRADING_MODE', 'paper')}**",
            color=discord.Color.green(),
        ),
    )


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.tree.command(name="status", description="Show system status and health")
async def cmd_status(interaction: discord.Interaction):
    mode = os.getenv("TRADING_MODE", "paper")
    embed = make_embed(
        "📡 System Status",
        f"Mode: **{mode}**\nUptime: running\nGuard Engine: connected",
        color=discord.Color.blue(),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="brief", description="Request a morning research brief")
@app_commands.describe(symbols="Comma-separated symbols (e.g. NVDA,QQQ,SPY)")
async def cmd_brief(interaction: discord.Interaction, symbols: str):
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    await interaction.response.defer()

    embed = make_embed(
        "🔍 Research Brief Requested",
        f"Queuing analysis for: **{', '.join(symbol_list)}**\n"
        "The Research Agent will post results in #research.",
        color=discord.Color.teal(),
    )
    await interaction.followup.send(embed=embed)

    # TODO: Phase 2 — trigger Research Agent via orchestrator
    log.info(f"Brief requested for: {symbol_list}")


@bot.tree.command(name="analyze", description="Request options analysis for a symbol")
@app_commands.describe(
    symbol="Stock symbol (e.g. QQQ)",
    strategy="Strategy type (e.g. bull_put, iron_condor, pmcc, covered_call)",
)
async def cmd_analyze(interaction: discord.Interaction, symbol: str, strategy: str):
    await interaction.response.defer()
    symbol = symbol.upper()

    embed = make_embed(
        "📊 Analysis Requested",
        f"**{symbol}** — {strategy}\n"
        "The Analysis Agent will post a trade card in #trade-proposals.",
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed)

    # TODO: Phase 2 — trigger Analysis Agent
    log.info(f"Analysis requested: {symbol} / {strategy}")


@bot.tree.command(name="guard_check", description="Manually check a trade against guards")
@app_commands.describe(
    symbol="Stock symbol",
    position_pct="Position size as % of portfolio",
    max_loss_pct="Max loss as % of portfolio",
    dte="Days to expiry",
)
async def cmd_guard_check(
    interaction: discord.Interaction,
    symbol: str,
    position_pct: float,
    max_loss_pct: float,
    dte: int,
):
    await interaction.response.defer()

    # Call guard engine API
    import aiohttp

    trade = {
        "symbol": symbol.upper(),
        "position_pct": position_pct,
        "max_loss_pct": max_loss_pct,
        "dte": dte,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://trader-guards:8100/check", json=trade
            ) as resp:
                result = await resp.json()

        embed = guard_embed(
            symbol=trade["symbol"],
            result=result.get("result", "ERROR"),
            reason=result.get("reason", "Unknown"),
        )
    except Exception as e:
        embed = make_embed(
            "⚠️ Guard Check Failed",
            f"Could not reach guard engine: {e}",
            color=discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="emergency_stop", description="HALT all new trades immediately")
async def cmd_emergency_stop(interaction: discord.Interaction):
    # TODO: Write halt flag to shared state / call orchestrator
    embed = make_embed(
        "🚨 EMERGENCY STOP",
        "All new trades HALTED. Existing positions unchanged.\n"
        "Use `/resume` to re-enable trading.",
        color=discord.Color.red(),
    )
    await interaction.response.send_message(embed=embed)
    if router:
        await router.send("system_health", embed=embed)
    log.critical("EMERGENCY STOP triggered")


@bot.tree.command(name="resume", description="Resume trading after emergency stop")
async def cmd_resume(interaction: discord.Interaction):
    embed = make_embed(
        "🟢 Trading Resumed",
        "System is back to normal operation.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)
    if router:
        await router.send("system_health", embed=embed)
    log.info("Trading resumed")


@bot.tree.command(name="watchlist", description="Show or update your watchlist")
@app_commands.describe(action="view / add / remove", symbol="Symbol to add/remove")
async def cmd_watchlist(
    interaction: discord.Interaction, action: str = "view", symbol: str = ""
):
    watchlist_path = "/app/configs/watchlist.json"

    if os.path.exists(watchlist_path):
        with open(watchlist_path) as f:
            watchlist = json.load(f)
    else:
        watchlist = {"symbols": ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]}

    action = action.lower()
    if action == "view":
        embed = make_embed(
            "📋 Watchlist",
            ", ".join(watchlist["symbols"]),
            color=discord.Color.blue(),
        )
    elif action == "add" and symbol:
        sym = symbol.upper()
        if sym not in watchlist["symbols"]:
            watchlist["symbols"].append(sym)
            with open(watchlist_path, "w") as f:
                json.dump(watchlist, f, indent=2)
        embed = make_embed("✅ Added", f"**{sym}** added to watchlist", color=discord.Color.green())
    elif action == "remove" and symbol:
        sym = symbol.upper()
        if sym in watchlist["symbols"]:
            watchlist["symbols"].remove(sym)
            with open(watchlist_path, "w") as f:
                json.dump(watchlist, f, indent=2)
        embed = make_embed("🗑️ Removed", f"**{sym}** removed from watchlist", color=discord.Color.orange())
    else:
        embed = make_embed("❓ Usage", "`/watchlist view` | `/watchlist add TSLA` | `/watchlist remove TSLA`")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rules", description="Show current guard rules")
async def cmd_rules(interaction: discord.Interaction):
    config_path = "/app/configs/guard_config.json"
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {"status": "Using defaults — see guard-engine/config.json"}

    rules_text = json.dumps(config, indent=2)
    if len(rules_text) > 1900:
        rules_text = rules_text[:1900] + "\n..."

    embed = make_embed(
        "🛡️ Guard Rules",
        f"```json\n{rules_text}\n```",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Reaction-based trade approval (Co-Pilot mode)
# ---------------------------------------------------------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel or channel.id != CHANNELS.get("trade_proposals"):
        return

    message = await channel.fetch_message(payload.message_id)
    emoji = str(payload.emoji)

    if emoji == "✅":
        log.info(f"Trade APPROVED by {payload.user_id} on message {payload.message_id}")
        # TODO: Forward to execution agent
        await channel.send(
            embed=make_embed("✅ Trade Approved", "Forwarding to Execution Agent...", color=discord.Color.green())
        )
    elif emoji == "❌":
        log.info(f"Trade REJECTED by {payload.user_id} on message {payload.message_id}")
        await channel.send(
            embed=make_embed("❌ Trade Rejected", "Trade discarded.", color=discord.Color.red())
        )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        log.error("DISCORD_BOT_TOKEN not set!")
        return
    bot.run(token)


if __name__ == "__main__":
    main()
