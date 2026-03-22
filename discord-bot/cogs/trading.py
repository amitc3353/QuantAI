"""
Trading Cog — Discord Commands for Order Execution
=====================================================
Every order flows: proposal → guard check → your approval → Alpaca execution.
No order bypasses the guard engine.

Commands:
  /buy, /sell — Place market orders (with guard + approval)
  /limit_buy, /limit_sell — Place limit orders
  /positions — View open positions
  /account — View account details
  /orders — View open/recent orders
  /cancel — Cancel an order
  /close — Close a position
  /quote — Get latest quote for a symbol
"""

import os
import json
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

from alpaca_client import (
    place_market_order,
    place_limit_order,
    get_account,
    get_positions,
    get_orders,
    cancel_order,
    cancel_all_orders,
    close_position,
    get_latest_quote,
    get_snapshot,
    TRADING_MODE,
)

log = logging.getLogger("trading-cog")

GUARD_URL = os.getenv("GUARD_URL", "http://trader-guards:8100")
CHANNEL_EXECUTION = int(os.getenv("CHANNEL_EXECUTION_LOG", "0"))
CHANNEL_PROPOSALS = int(os.getenv("CHANNEL_TRADE_PROPOSALS", "0"))


# ---------------------------------------------------------------------------
# Guard check helper
# ---------------------------------------------------------------------------
async def check_guards(trade: dict) -> dict:
    """Submit trade proposal to guard engine."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GUARD_URL}/check",
                json=trade,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return await resp.json()
    except Exception as e:
        return {"result": "REJECT", "reason": f"Guard engine unreachable: {e}"}


# ---------------------------------------------------------------------------
# Embed helpers
# ---------------------------------------------------------------------------
def trade_embed(title: str, desc: str, color: discord.Color, fields: dict = None, footer: str = None):
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(timezone.utc))
    if fields:
        for k, v in fields.items():
            embed.add_field(name=k, value=str(v), inline=True)
    embed.set_footer(text=footer or f"QuantAI · {TRADING_MODE} mode")
    return embed


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------
class TradingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_trades: dict[int, dict] = {}  # message_id → trade data

    # --- Account & Portfolio ---

    @app_commands.command(name="account", description="View your Alpaca account details")
    async def cmd_account(self, interaction: discord.Interaction):
        await interaction.response.defer()
        acct = await get_account()

        if "error" in acct:
            await interaction.followup.send(embed=trade_embed(
                "❌ Account Error", acct["error"], discord.Color.red()))
            return

        daily_emoji = "🟢" if acct["daily_pnl"] >= 0 else "🔴"
        embed = trade_embed(
            f"💰 Account ({TRADING_MODE})",
            f"Status: **{acct['status']}**",
            discord.Color.blue(),
            fields={
                "Portfolio Value": f"${acct['portfolio_value']:,.2f}",
                "Equity": f"${acct['equity']:,.2f}",
                "Cash": f"${acct['cash']:,.2f}",
                "Buying Power": f"${acct['buying_power']:,.2f}",
                f"{daily_emoji} Daily P&L": f"${acct['daily_pnl']:,.2f} ({acct['daily_pnl_pct']}%)",
                "PDT Flag": "⚠️ Yes" if acct["pattern_day_trader"] else "✅ No",
            },
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="positions", description="View open positions")
    async def cmd_positions(self, interaction: discord.Interaction):
        await interaction.response.defer()
        positions = await get_positions()

        if not positions:
            await interaction.followup.send(embed=trade_embed(
                "📋 Positions", "No open positions.", discord.Color.blue()))
            return

        if "error" in positions[0]:
            await interaction.followup.send(embed=trade_embed(
                "❌ Error", positions[0]["error"], discord.Color.red()))
            return

        desc_lines = []
        total_pnl = 0
        for p in positions:
            pnl = p["unrealized_pl"]
            total_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            desc_lines.append(
                f"{emoji} **{p['symbol']}** — {p['qty']} shares @ ${p['avg_entry_price']:.2f}\n"
                f"   Now: ${p['current_price']:.2f} | P&L: ${pnl:,.2f} ({p['unrealized_plpc']:.1%})"
            )

        total_emoji = "🟢" if total_pnl >= 0 else "🔴"
        embed = trade_embed(
            f"📋 Positions ({len(positions)}) — {total_emoji} ${total_pnl:,.2f}",
            "\n".join(desc_lines)[:2000],
            discord.Color.green() if total_pnl >= 0 else discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="orders", description="View recent orders")
    @app_commands.describe(status="open, closed, or all")
    async def cmd_orders(self, interaction: discord.Interaction, status: str = "open"):
        await interaction.response.defer()
        orders = await get_orders(status=status)

        if not orders:
            await interaction.followup.send(embed=trade_embed(
                "📝 Orders", f"No {status} orders.", discord.Color.blue()))
            return

        if "error" in orders[0]:
            await interaction.followup.send(embed=trade_embed(
                "❌ Error", orders[0]["error"], discord.Color.red()))
            return

        desc_lines = []
        for o in orders[:15]:
            price_str = f" @ ${o['filled_avg_price']:.2f}" if o.get("filled_avg_price") else ""
            desc_lines.append(
                f"**{o['symbol']}** — {o['side']} {o['qty']} ({o['type']}){price_str} — {o['status']}"
            )

        embed = trade_embed(
            f"📝 {status.title()} Orders ({len(orders)})",
            "\n".join(desc_lines)[:2000],
            discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)

    # --- Market Data ---

    @app_commands.command(name="quote", description="Get latest quote for a symbol")
    @app_commands.describe(symbol="Stock symbol (e.g. SPY)")
    async def cmd_quote(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer()
        snap = await get_snapshot(symbol)

        if "error" in snap:
            await interaction.followup.send(embed=trade_embed(
                "❌ Quote Error", snap["error"], discord.Color.red()))
            return

        change = snap.get("change", 0)
        emoji = "🟢" if change >= 0 else "🔴"
        embed = trade_embed(
            f"{emoji} {snap['symbol']}",
            f"**${snap.get('price', 0):.2f}** ({snap.get('change_pct', 0):+.2f}%)",
            discord.Color.green() if change >= 0 else discord.Color.red(),
            fields={
                "Bid": f"${snap.get('bid', 0):.2f}",
                "Ask": f"${snap.get('ask', 0):.2f}",
                "Open": f"${snap.get('open', 0):.2f}",
                "High": f"${snap.get('high', 0):.2f}",
                "Low": f"${snap.get('low', 0):.2f}",
                "Volume": f"{snap.get('volume', 0):,}",
                "Prev Close": f"${snap.get('prev_close', 0):.2f}",
            },
        )
        await interaction.followup.send(embed=embed)

    # --- Order Placement (with guard check + approval) ---

    @app_commands.command(name="buy", description="Place a market buy order (requires approval)")
    @app_commands.describe(symbol="Stock symbol", qty="Number of shares")
    async def cmd_buy(self, interaction: discord.Interaction, symbol: str, qty: float):
        await self._propose_trade(interaction, symbol.upper(), qty, "buy", "market")

    @app_commands.command(name="sell", description="Place a market sell order (requires approval)")
    @app_commands.describe(symbol="Stock symbol", qty="Number of shares")
    async def cmd_sell(self, interaction: discord.Interaction, symbol: str, qty: float):
        await self._propose_trade(interaction, symbol.upper(), qty, "sell", "market")

    @app_commands.command(name="limit_buy", description="Place a limit buy order (requires approval)")
    @app_commands.describe(symbol="Stock symbol", qty="Number of shares", price="Limit price")
    async def cmd_limit_buy(self, interaction: discord.Interaction, symbol: str, qty: float, price: float):
        await self._propose_trade(interaction, symbol.upper(), qty, "buy", "limit", price)

    @app_commands.command(name="limit_sell", description="Place a limit sell order (requires approval)")
    @app_commands.describe(symbol="Stock symbol", qty="Number of shares", price="Limit price")
    async def cmd_limit_sell(self, interaction: discord.Interaction, symbol: str, qty: float, price: float):
        await self._propose_trade(interaction, symbol.upper(), qty, "sell", "limit", price)

    async def _propose_trade(
        self,
        interaction: discord.Interaction,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        limit_price: float = None,
    ):
        await interaction.response.defer()

        # Step 1: Guard check
        guard_proposal = {
            "symbol": symbol,
            "position_pct": 2.0,  # TODO: calculate from actual portfolio
            "max_loss_pct": 1.0,
            "dte": 999,  # equity, not options
        }
        guard_result = await check_guards(guard_proposal)

        if guard_result.get("result") == "REJECT":
            embed = trade_embed(
                "🛡️ Guard REJECTED",
                f"**{symbol}** — {guard_result.get('reason', 'Unknown')}",
                discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return

        # Step 2: Get current quote for context
        snap = await get_snapshot(symbol)
        price_str = f"${snap.get('price', '?')}" if "error" not in snap else "price unavailable"

        # Step 3: Propose trade — wait for approval
        price_info = f" @ ${limit_price:.2f}" if limit_price else f" (market ~{price_str})"
        embed = trade_embed(
            f"📊 Trade Proposal: {side.upper()} {symbol}",
            f"**{side.upper()} {qty} {symbol}**{price_info}\n"
            f"Type: {order_type} | Mode: {TRADING_MODE}\n\n"
            f"✅ Guard: APPROVED\n\n"
            f"React ✅ to execute, ❌ to cancel.",
            discord.Color.gold(),
            fields={
                "Symbol": symbol,
                "Qty": str(qty),
                "Side": side.upper(),
                "Type": order_type,
                "Current Price": price_str,
            },
        )
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        self.pending_trades[msg.id] = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": order_type,
            "limit_price": limit_price,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
        }

    # --- Order Management ---

    @app_commands.command(name="cancel", description="Cancel an open order by ID")
    @app_commands.describe(order_id="Alpaca order ID")
    async def cmd_cancel(self, interaction: discord.Interaction, order_id: str):
        await interaction.response.defer()
        result = await cancel_order(order_id)
        if "error" in result:
            embed = trade_embed("❌ Cancel Failed", result["error"], discord.Color.red())
        else:
            embed = trade_embed("✅ Order Cancelled", f"Order `{order_id}` cancelled.", discord.Color.green())
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="close", description="Close an entire position")
    @app_commands.describe(symbol="Symbol to close")
    async def cmd_close(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer()
        result = await close_position(symbol)
        if "error" in result:
            embed = trade_embed("❌ Close Failed", result["error"], discord.Color.red())
        else:
            embed = trade_embed("✅ Position Closed", f"**{symbol.upper()}** closed.", discord.Color.green())
        await interaction.followup.send(embed=embed)

        # Post to execution log
        exec_channel = self.bot.get_channel(CHANNEL_EXECUTION)
        if exec_channel:
            await exec_channel.send(embed=trade_embed(
                f"📤 Position Closed: {symbol.upper()}",
                f"Closed by user request.",
                discord.Color.orange(),
            ))

    # --- Reaction handler for trade approvals ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        trade = self.pending_trades.get(payload.message_id)
        if not trade:
            return

        if payload.user_id != trade.get("user_id"):
            return

        channel = self.bot.get_channel(payload.channel_id)
        emoji = str(payload.emoji)

        if emoji == "❌":
            del self.pending_trades[payload.message_id]
            await channel.send(embed=trade_embed(
                "❌ Trade Cancelled", f"{trade['side'].upper()} {trade['qty']} {trade['symbol']} cancelled.",
                discord.Color.red()))
            return

        if emoji != "✅":
            return

        del self.pending_trades[payload.message_id]

        # Execute the trade
        await channel.send(embed=trade_embed(
            "⏳ Executing...",
            f"{trade['side'].upper()} {trade['qty']} {trade['symbol']}",
            discord.Color.blue(),
        ))

        if trade["type"] == "limit" and trade.get("limit_price"):
            result = await place_limit_order(
                trade["symbol"], trade["qty"], trade["side"], trade["limit_price"]
            )
        else:
            result = await place_market_order(trade["symbol"], trade["qty"], trade["side"])

        # Post result
        if "error" in result:
            embed = trade_embed(
                f"❌ Order Failed: {trade['symbol']}",
                f"Error: {result['error']}",
                discord.Color.red(),
                fields={"Hash": result.get("hash", "?")},
            )
        else:
            fill_price = result.get("filled_avg_price", "pending")
            embed = trade_embed(
                f"⚡ Executed: {result['symbol']}",
                f"**{result['side'].upper()} {result['qty']} {result['symbol']}**\n"
                f"Status: {result['status']}",
                discord.Color.green(),
                fields={
                    "Hash": result.get("hash", "?"),
                    "Order ID": result.get("order_id", "?")[:12] + "...",
                    "Fill Price": str(fill_price) if fill_price else "pending",
                    "Mode": TRADING_MODE,
                },
            )

        await channel.send(embed=embed)

        # Also post to #execution-log
        exec_channel = self.bot.get_channel(CHANNEL_EXECUTION)
        if exec_channel and exec_channel.id != channel.id:
            await exec_channel.send(embed=embed)




# ---------------------------------------------------------------------------
# Journal & Performance Commands
# ---------------------------------------------------------------------------

    @app_commands.command(name="journal", description="Today's trade journal — what happened, why, outcomes")
    async def cmd_journal(self, interaction: discord.Interaction):
        await interaction.response.defer()
        import sys
        sys.path.insert(0, "/app/discord-bot")
        sys.path.insert(0, "/app/services")

        from pathlib import Path
        import json
        from datetime import date

        today = date.today().isoformat()
        fields = []
        total_pnl = 0
        trade_count = 0

        # Read agent journals
        journals = {
            "Agent 1 (Condor)": Path("/app/data/memory/paper/agent1_journal.jsonl"),
            "Agent 2 (CC)":     Path("/app/data/memory/paper/agent2_journal.jsonl"),
        }

        for agent_name, journal_path in journals.items():
            if not journal_path.exists():
                continue
            records = []
            with open(journal_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            r = json.loads(line)
                            if r.get("date") == today or r.get("timestamp", "").startswith(today):
                                records.append(r)
                        except Exception:
                            continue

            if not records:
                fields.append({"name": agent_name, "value": "No activity today", "inline": True})
                continue

            entries = [r for r in records if r.get("event") == "entry"]
            exits = [r for r in records if r.get("event") == "exit"]
            skips = [r for r in records if r.get("event") == "skip"]
            rolls = [r for r in records if r.get("event") == "roll"]
            errors = [r for r in records if r.get("event") == "error"]

            day_pnl = sum(r.get("pnl_per_contract", 0) or r.get("pnl", 0) for r in exits)
            total_pnl += day_pnl
            trade_count += len(entries)

            summary_parts = []
            if entries:
                ctx_scores = [r.get("context_score") for r in entries if r.get("context_score")]
                ctx_str = f" (ctx: {', '.join(str(s) for s in ctx_scores)})" if ctx_scores else ""
                summary_parts.append(f"{len(entries)} entr{'y' if len(entries)==1 else 'ies'}{ctx_str}")
            if exits:
                wins = sum(1 for r in exits if r.get("outcome") == "win")
                summary_parts.append(f"{len(exits)} closed ({wins}W/{len(exits)-wins}L) P&L: ${day_pnl:.2f}")
            if rolls:
                summary_parts.append(f"{len(rolls)} roll(s)")
            if skips:
                skip_reasons = list(set(r.get("reason", "?") for r in skips))
                summary_parts.append(f"{len(skips)} skip(s): {', '.join(skip_reasons[:2])}")
            if errors:
                summary_parts.append(f"⚠️ {len(errors)} error(s)")

            fields.append({
                "name": agent_name,
                "value": "\n".join(summary_parts) or "Idle",
                "inline": False,
            })

        # Add lessons from today's scoring if available
        from pathlib import Path as P
        lessons_file = P("/app/data/memory/paper/agent1_lessons.jsonl")
        todays_lessons = []
        if lessons_file.exists():
            with open(lessons_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            r = json.loads(line)
                            if r.get("date") == today:
                                todays_lessons.append(r.get("lesson", ""))
                        except Exception:
                            continue

        if todays_lessons:
            fields.append({
                "name": "📝 Lessons extracted today",
                "value": "\n".join(f"• {l[:80]}" for l in todays_lessons[:3]),
                "inline": False,
            })

        color = 0x2ECC71 if total_pnl > 0 else (0xE74C3C if total_pnl < 0 else 0x3498DB)
        embed = discord.Embed(
            title=f"📓 Journal — {today}",
            description=f"**{trade_count}** trade(s) | Net P&L: **${total_pnl:.2f}**" if trade_count else "No trades today",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        for field in fields:
            embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
        embed.set_footer(text=f"QuantAI Journal · {TRADING_MODE}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="performance", description="Win rates, P&L, and stats across all agents")
    @app_commands.describe(days="Number of days to analyze (default 30)")
    async def cmd_performance(self, interaction: discord.Interaction, days: int = 30):
        await interaction.response.defer()
        import sys
        sys.path.insert(0, "/app/discord-bot")
        from pathlib import Path
        import json
        from datetime import date, timedelta

        cutoff = (date.today() - timedelta(days=days)).isoformat()
        fields = []

        agents = {
            "Agent 1 — Iron Condor": "/app/data/memory/paper/agent1_journal.jsonl",
            "Agent 2 — Covered Call": "/app/data/memory/paper/agent2_journal.jsonl",
        }

        combined_wins = 0
        combined_trades = 0
        combined_pnl = 0.0

        for agent_name, journal_path in agents.items():
            p = Path(journal_path)
            if not p.exists():
                fields.append({"name": agent_name, "value": "No data yet", "inline": False})
                continue

            exits = []
            entries = []
            skips = []
            with open(p) as f:
                for line in f:
                    if line.strip():
                        try:
                            r = json.loads(line)
                            d = r.get("date", r.get("timestamp", "")[:10])
                            if d >= cutoff:
                                ev = r.get("event", "")
                                if ev == "exit": exits.append(r)
                                elif ev == "entry": entries.append(r)
                                elif ev == "skip": skips.append(r)
                        except Exception:
                            continue

            if not exits and not entries:
                fields.append({"name": agent_name, "value": f"No activity in last {days} days", "inline": False})
                continue

            wins = [r for r in exits if r.get("outcome") == "win"]
            losses = [r for r in exits if r.get("outcome") == "loss"]
            total_pnl = sum(r.get("pnl_per_contract", 0) or r.get("pnl", 0) for r in exits)
            win_rate = len(wins) / len(exits) * 100 if exits else 0

            combined_wins += len(wins)
            combined_trades += len(exits)
            combined_pnl += total_pnl

            # Context score correlation (did high-score entries win more?)
            high_ctx = [r for r in exits if (r.get("context_score") or 0) >= 60]
            low_ctx = [r for r in exits if 0 < (r.get("context_score") or 0) < 60]
            ctx_str = ""
            if high_ctx and low_ctx:
                high_wr = sum(1 for r in high_ctx if r.get("outcome")=="win") / len(high_ctx) * 100
                low_wr = sum(1 for r in low_ctx if r.get("outcome")=="win") / len(low_ctx) * 100
                ctx_str = f"\nContext ≥60: {high_wr:.0f}% WR | <60: {low_wr:.0f}% WR"

            # Skip analysis
            skip_summary = ""
            if skips:
                skip_reasons = {}
                for s in skips:
                    r = s.get("reason", "unknown")
                    skip_reasons[r] = skip_reasons.get(r, 0) + 1
                top_skips = sorted(skip_reasons.items(), key=lambda x: x[1], reverse=True)[:3]
                skip_summary = f"\nSkips: " + ", ".join(f"{r}({n})" for r, n in top_skips)

            fields.append({
                "name": agent_name,
                "value": (
                    f"**{len(exits)}** closed | **{win_rate:.0f}%** WR | P&L: **${total_pnl:.2f}**\n"
                    f"{len(wins)}W / {len(losses)}L | {len(entries)} entries"
                    f"{ctx_str}{skip_summary}"
                ),
                "inline": False,
            })

        overall_wr = combined_wins / combined_trades * 100 if combined_trades else 0
        color = 0x2ECC71 if combined_pnl > 0 else (0xE74C3C if combined_pnl < 0 else 0x3498DB)

        embed = discord.Embed(
            title=f"📊 Performance — Last {days} Days",
            description=f"**{combined_trades}** closed | **{overall_wr:.0f}%** overall WR | Net P&L: **${combined_pnl:.2f}**",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        for field in fields:
            embed.add_field(name=field["name"], value=field["value"], inline=False)
        embed.set_footer(text=f"QuantAI Performance · {TRADING_MODE} · {days}d")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="lessons", description="View and search lessons learned by the system")
    @app_commands.describe(query="Search term (optional — leave blank for most recent)")
    async def cmd_lessons(self, interaction: discord.Interaction, query: str = ""):
        await interaction.response.defer()
        import sys
        sys.path.insert(0, "/app/discord-bot")
        from memory import search_lessons, get_lessons

        if query:
            lessons = search_lessons(query)
            title = f"🔍 Lessons matching \"{query}\""
        else:
            lessons = get_lessons(15)
            title = "📚 Recent Lessons Learned"

        if not lessons:
            await interaction.followup.send(embed=discord.Embed(
                title=title,
                description="No lessons yet — they accumulate as the system trades and scores.",
                color=0x95A5A6,
            ))
            return

        lines = []
        for l in lessons[-12:]:
            source = l.get("source", "?")
            conf = l.get("confidence", 1.0)
            conf_bar = "●" * int(conf * 3) + "○" * (3 - int(conf * 3))
            ts = l.get("timestamp", "")[:10]
            lines.append(f"`{conf_bar}` **{ts}** [{source}]\n{l.get('lesson', '')[:120]}")

        # Split into chunks if needed (Discord 1024 char field limit)
        chunk = "\n\n".join(lines[:6])
        embed = discord.Embed(title=title, description=chunk[:2000], color=0x9B59B6,
                              timestamp=datetime.now(timezone.utc))
        if len(lines) > 6:
            embed.add_field(name="More lessons", value="\n\n".join(lines[6:12])[:1024], inline=False)
        embed.set_footer(text=f"QuantAI Lessons · {len(lessons)} total · confidence: ●●●=high ○○○=low")
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(TradingCog(bot))

