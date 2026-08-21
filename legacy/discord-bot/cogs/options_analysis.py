"""
Options Analysis Cog — Strategy Analysis via Discord
======================================================
Commands for analyzing options strategies with real Greeks.
Uses py_vollib locally — zero API cost, sub-millisecond.

Commands:
  /greeks — Compute Greeks for a single option
  /bull_put — Analyze a bull put spread
  /iron_condor — Analyze an iron condor
  /covered_call — Analyze a covered call
"""

import os
import logging
from datetime import datetime, timezone
from dataclasses import asdict

import discord
from discord.ext import commands
from discord import app_commands

from greeks_engine import (
    analyze_option,
    analyze_bull_put_spread,
    analyze_iron_condor,
    analyze_covered_call,
    compute_greeks,
    dte_to_years,
    spread_to_dict,
)
from alpaca_client import get_snapshot, TRADING_MODE

log = logging.getLogger("options-cog")

CHANNEL_PROPOSALS = int(os.getenv("CHANNEL_TRADE_PROPOSALS", "0"))


# ---------------------------------------------------------------------------
# Embed helpers
# ---------------------------------------------------------------------------
def greeks_embed(pricing) -> discord.Embed:
    """Build embed for single option Greeks."""
    g = pricing.greeks
    color = discord.Color.green() if pricing.option_type == "call" else discord.Color.red()

    embed = discord.Embed(
        title=f"📊 {pricing.symbol} {pricing.strike}{pricing.option_type[0].upper()} {pricing.expiry_dte}DTE",
        description=f"**{pricing.moneyness}** | IV: {pricing.iv:.1%} | Price: ${pricing.theoretical_price:.2f}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Delta", value=f"{g.delta:+.4f}", inline=True)
    embed.add_field(name="Gamma", value=f"{g.gamma:.6f}", inline=True)
    embed.add_field(name="Theta", value=f"${g.theta:.4f}/day", inline=True)
    embed.add_field(name="Vega", value=f"${g.vega:.4f}", inline=True)
    embed.add_field(name="Rho", value=f"${g.rho:.4f}", inline=True)
    embed.add_field(name="Underlying", value=f"${pricing.underlying_price:.2f}", inline=True)
    embed.add_field(name="Intrinsic", value=f"${pricing.intrinsic_value:.2f}", inline=True)
    embed.add_field(name="Extrinsic", value=f"${pricing.extrinsic_value:.2f}", inline=True)
    embed.set_footer(text=f"QuantAI Greeks Engine · {TRADING_MODE}")
    return embed


def spread_embed(spread, title_prefix: str = "📊") -> discord.Embed:
    """Build embed for a spread analysis."""
    strategy_names = {
        "bull_put_spread": "Bull Put Spread",
        "iron_condor": "Iron Condor",
        "covered_call": "Covered Call",
    }
    name = strategy_names.get(spread.strategy, spread.strategy)

    # Color by credit/debit
    color = discord.Color.teal() if spread.net_credit > 0 else discord.Color.blue()

    # Breakeven string
    if isinstance(spread.breakeven, list):
        be_str = " / ".join(f"${b:.2f}" for b in spread.breakeven)
    else:
        be_str = f"${spread.breakeven:.2f}"

    # Legs description
    legs_str = ""
    for leg in spread.legs:
        action = leg.get("action", "?").upper()
        ltype = leg.get("type", "?")
        strike = leg.get("strike", "")
        price = leg.get("price", "")
        delta = leg.get("delta", "")
        strike_str = f" ${strike}" if strike else ""
        price_str = f" @ ${price}" if price else ""
        delta_str = f" (Δ{delta:+.2f})" if isinstance(delta, (int, float)) else ""
        legs_str += f"**{action}** {ltype}{strike_str}{price_str}{delta_str}\n"

    embed = discord.Embed(
        title=f"{title_prefix} {name}: {spread.symbol}",
        description=legs_str,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    # P&L section
    embed.add_field(name="Max Profit", value=f"${spread.max_profit:.2f}", inline=True)
    embed.add_field(name="Max Loss", value=f"${spread.max_loss:.2f}", inline=True)
    embed.add_field(name="Risk/Reward", value=f"{spread.risk_reward_ratio:.1f}x", inline=True)

    if spread.net_credit > 0:
        embed.add_field(name="Credit", value=f"${spread.net_credit:.2f}", inline=True)
    else:
        embed.add_field(name="Debit", value=f"${spread.net_debit:.2f}", inline=True)

    embed.add_field(name="Breakeven", value=be_str, inline=True)
    embed.add_field(name="P(Profit)", value=f"{spread.pop:.0%}", inline=True)

    # Greeks
    embed.add_field(name="Net Delta", value=f"{spread.net_delta:+.4f}", inline=True)
    embed.add_field(name="Net Theta", value=f"${spread.net_theta:.4f}/day", inline=True)
    embed.add_field(name="Ann. Return", value=f"{spread.annualized_return:.1f}%", inline=True)

    embed.set_footer(text=f"QuantAI Greeks Engine · {TRADING_MODE} · React ✅ to propose trade")
    return embed


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------
class OptionsAnalysisCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="greeks", description="Compute Greeks for a single option")
    @app_commands.describe(
        symbol="Stock symbol (e.g. SPY)",
        strike="Strike price",
        dte="Days to expiry",
        option_type="call or put",
        iv="Implied volatility (e.g. 0.25 for 25%)",
    )
    async def cmd_greeks(
        self,
        interaction: discord.Interaction,
        symbol: str,
        strike: float,
        dte: int,
        option_type: str = "call",
        iv: float = 0.25,
    ):
        await interaction.response.defer()

        # Get current price from Alpaca
        snap = await get_snapshot(symbol)
        price = snap.get("price", strike)  # fallback to strike if no data

        pricing = analyze_option(
            symbol=symbol.upper(),
            strike=strike,
            dte=dte,
            option_type=option_type,
            underlying_price=price,
            iv=iv,
        )

        await interaction.followup.send(embed=greeks_embed(pricing))

    @app_commands.command(name="bull_put", description="Analyze a bull put spread")
    @app_commands.describe(
        symbol="Stock symbol",
        short_strike="Short put strike (higher)",
        long_strike="Long put strike (lower)",
        dte="Days to expiry",
        iv="Implied volatility (e.g. 0.25)",
    )
    async def cmd_bull_put(
        self,
        interaction: discord.Interaction,
        symbol: str,
        short_strike: float,
        long_strike: float,
        dte: int,
        iv: float = 0.25,
    ):
        await interaction.response.defer()
        symbol = symbol.upper()

        snap = await get_snapshot(symbol)
        price = snap.get("price", short_strike)

        spread = analyze_bull_put_spread(
            symbol=symbol,
            underlying_price=price,
            short_strike=short_strike,
            long_strike=long_strike,
            dte=dte,
            short_iv=iv,
            long_iv=iv * 1.02,  # Slight skew approximation
        )

        await interaction.followup.send(embed=spread_embed(spread))

    @app_commands.command(name="iron_condor", description="Analyze an iron condor")
    @app_commands.describe(
        symbol="Stock symbol",
        put_short="Short put strike",
        put_long="Long put strike (lower)",
        call_short="Short call strike",
        call_long="Long call strike (higher)",
        dte="Days to expiry",
        iv="Implied volatility",
    )
    async def cmd_iron_condor(
        self,
        interaction: discord.Interaction,
        symbol: str,
        put_short: float,
        put_long: float,
        call_short: float,
        call_long: float,
        dte: int,
        iv: float = 0.25,
    ):
        await interaction.response.defer()
        symbol = symbol.upper()

        snap = await get_snapshot(symbol)
        price = snap.get("price", (put_short + call_short) / 2)

        spread = analyze_iron_condor(
            symbol=symbol,
            underlying_price=price,
            put_short=put_short,
            put_long=put_long,
            call_short=call_short,
            call_long=call_long,
            dte=dte,
            put_short_iv=iv,
            put_long_iv=iv * 1.03,
            call_short_iv=iv * 0.97,
            call_long_iv=iv,
        )

        await interaction.followup.send(embed=spread_embed(spread))

    @app_commands.command(name="covered_call", description="Analyze a covered call")
    @app_commands.describe(
        symbol="Stock symbol",
        strike="Call strike to sell",
        dte="Days to expiry",
        iv="Implied volatility",
        cost_basis="Your cost basis per share (optional)",
    )
    async def cmd_covered_call(
        self,
        interaction: discord.Interaction,
        symbol: str,
        strike: float,
        dte: int,
        iv: float = 0.20,
        cost_basis: float = None,
    ):
        await interaction.response.defer()
        symbol = symbol.upper()

        snap = await get_snapshot(symbol)
        price = snap.get("price", strike)

        spread = analyze_covered_call(
            symbol=symbol,
            underlying_price=price,
            strike=strike,
            dte=dte,
            iv=iv,
            cost_basis=cost_basis,
        )

        await interaction.followup.send(embed=spread_embed(spread))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(OptionsAnalysisCog(bot))
