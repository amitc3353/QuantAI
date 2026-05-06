#!/usr/bin/env python3
"""
QuantAI EOD Trade Summary
Posts a daily trade summary to Discord at market close.
Shows Agent Alpha, Agent Beta, and Agent Gamma trades for the day.

Called by run_pipeline.py in eod mode, or directly:
  python3 eod_summary.py
"""

import json, os, requests
from datetime import datetime, date
from zoneinfo import ZoneInfo

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")
JOURNAL  = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
DISCORD_CHANNEL = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
today    = date.today().isoformat()

def post(msg):
    from _discord import post_to_channel
    if not DISCORD_CHANNEL:
        print(msg)
        return
    for chunk in [msg[i:i+1900] for i in range(0, len(msg), 1900)]:
        if not post_to_channel(DISCORD_CHANNEL, chunk):
            print(f"Discord post failed (chunk len={len(chunk)})")

def format_trade(t):
    """One-line trade summary."""
    strategy = t.get("strategy", t.get("action", "?")).replace("_", " ").upper()
    symbol   = t.get("symbol", "?")
    credit   = t.get("estimated_credit") or t.get("premium") or 0
    max_loss = t.get("max_loss_pct", 0)
    status   = t.get("status", "OPEN")
    pnl      = t.get("pnl", None)
    trade_id = t.get("id", "?")

    # P&L display
    if pnl is not None:
        pnl_str = f"P&L: ${pnl:+.0f}"
    elif status == "OPEN":
        pnl_str = "OPEN"
    else:
        pnl_str = "closed"

    # Legs summary for spreads
    legs = t.get("legs", [])
    if legs:
        leg_summary = " / ".join(
            f"{l.get('action','').upper()} ${l.get('strike','?')} {l.get('type','')}"
            for l in legs[:2]
        )
    else:
        strike = t.get("strike", "")
        expiry = t.get("expiry", "")
        leg_summary = f"${strike} {expiry}" if strike else ""

    return f"  `{trade_id}` {symbol} {strategy} {leg_summary} | Credit: ${credit:.2f} | {pnl_str}"

def build_summary():
    if not os.path.exists(JOURNAL):
        return "No trades logged yet."

    all_trades = [json.loads(l) for l in open(JOURNAL) if l.strip()]

    # Today's trades
    alpha_today = [t for t in all_trades if t.get("source") == "agent_alpha" and today in t.get("timestamp","")]
    beta_today  = [t for t in all_trades if t.get("source") == "agent_beta"  and today in t.get("timestamp","")]
    gamma_today = [t for t in all_trades if t.get("source") == "agent_gamma" and today in t.get("timestamp","")]

    # All-time open positions
    alpha_open  = [t for t in all_trades if t.get("source") == "agent_alpha" and t.get("status") == "OPEN"]
    beta_open   = [t for t in all_trades if t.get("source") == "agent_beta"  and t.get("status") == "OPEN"]
    gamma_open  = [t for t in all_trades if t.get("source") == "agent_gamma" and t.get("status") == "OPEN"]

    # All-time stats
    alpha_closed = [t for t in all_trades if t.get("source") == "agent_alpha" and t.get("status") == "CLOSED"]
    beta_closed  = [t for t in all_trades if t.get("source") == "agent_beta"  and t.get("status") == "CLOSED"]
    gamma_closed = [t for t in all_trades if t.get("source") == "agent_gamma" and t.get("status") == "CLOSED"]

    def win_rate(closed):
        if not closed: return "N/A"
        wins = len([t for t in closed if (t.get("pnl") or 0) > 0])
        return f"{wins/len(closed)*100:.0f}%"

    def total_pnl(closed):
        return sum(t.get("pnl") or 0 for t in closed)

    now_str = datetime.now(ET).strftime("%b %d, %Y")
    lines = [f"📊 **QuantAI Daily Summary — {now_str}**", ""]

    # ── Agent Alpha ──────────────────────────────────────────────────
    lines.append("🤖 **Agent Alpha** *(Bull put spreads & directional strategies)*")
    if alpha_today:
        lines.append(f"  Traded today: {len(alpha_today)}")
        for t in alpha_today:
            lines.append(format_trade(t))
    else:
        lines.append("  No trades today")
    if alpha_open:
        lines.append(f"  Open positions: {len(alpha_open)}")
        for t in alpha_open:
            lines.append(format_trade(t))
    lines.append(f"  All-time: {len(alpha_closed)} closed | Win rate: {win_rate(alpha_closed)} | P&L: ${total_pnl(alpha_closed):+.0f}")
    lines.append("")

    # ── Agent Beta ───────────────────────────────────────────────────
    lines.append("🤖 **Agent Beta** *(Iron condors & range-bound strategies)*")
    if beta_today:
        lines.append(f"  Traded today: {len(beta_today)}")
        for t in beta_today:
            lines.append(format_trade(t))
    else:
        lines.append("  No trades today")
    if beta_open:
        lines.append(f"  Open positions: {len(beta_open)}")
        for t in beta_open:
            lines.append(format_trade(t))
    lines.append(f"  All-time: {len(beta_closed)} closed | Win rate: {win_rate(beta_closed)} | P&L: ${total_pnl(beta_closed):+.0f}")
    lines.append("")

    # ── Agent Gamma ──────────────────────────────────────────────────
    lines.append("🤖 **Agent Gamma** *(RSI(10) mean-reversion on equity options)*")
    if gamma_today:
        lines.append(f"  Traded today: {len(gamma_today)}")
        for t in gamma_today:
            lines.append(format_trade(t))
    else:
        lines.append("  No trades today")
    if gamma_open:
        lines.append(f"  Open positions: {len(gamma_open)}")
        for t in gamma_open:
            lines.append(format_trade(t))
    lines.append(f"  All-time: {len(gamma_closed)} closed | Win rate: {win_rate(gamma_closed)} | P&L: ${total_pnl(gamma_closed):+.0f}")
    lines.append("")

    # ── Combined ─────────────────────────────────────────────────────
    all_closed = alpha_closed + beta_closed + gamma_closed
    all_open   = alpha_open + beta_open + gamma_open
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"**Total open:** {len(all_open)} | **Total closed:** {len(all_closed)}")
    if all_closed:
        lines.append(f"**Combined win rate:** {win_rate(all_closed)} | **Total P&L:** ${total_pnl(all_closed):+.0f}")
    lines.append("")
    lines.append("📱 Full detail: https://docs.google.com/spreadsheets/d/1GidIf-oLY9NfeRGVTwwGFYzA4eZx2bYjvY7UOATiMM0")
    lines.append("_Score today in #chat: `score today 78/100`_")

    return "\n".join(lines)


if __name__ == "__main__":
    summary = build_summary()
    post(summary)
    print("[eod_summary] Posted")
