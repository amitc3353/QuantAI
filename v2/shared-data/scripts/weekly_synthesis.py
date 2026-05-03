#!/usr/bin/env python3
"""weekly_synthesis — Friday Sonnet-driven aggregation + Discord report.

Runs Friday 4:45 PM ET via cron. For each agent that had activity this week:
  1. Aggregate closed trades, capability_diagnosis files, and trade_reviews.
  2. Call Sonnet once with all the week's data + agent identity.
  3. Format a per-agent summary (~1900 chars) for Discord #alerts.
  4. Write the full report to /root/quantai-v2/shared-data/weekly_reports/.

Sonnet failure → retry once → fall back to raw-data file + Discord error notice.

CLI:
  python3 weekly_synthesis.py [--dry-run] [--week-start YYYY-MM-DD]

  --dry-run  Don't post to Discord, don't write report file. Print to stdout.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")

from _journal_update import DEFAULT_JOURNAL
from _paths import (
    CAPABILITY_REQUESTS_DIR as REQUESTS_DIR,
    TRADE_REVIEWS_DIR as REVIEWS_DIR,
    WEEKLY_REPORTS_DIR as REPORTS_DIR,
)
from _decision_helpers import this_week_monday

ET = ZoneInfo("America/New_York")
REPO_AGENTS_DIR = Path("/home/trader/QuantAI/v2/shared-data/agents")
LLM_TIMEOUT = 90  # seconds — synthesis is bigger than per-trade calls

DISCORD_ALERTS_CH = os.environ.get("DISCORD_CHANNEL_ALERTS", "")

AGENTS = ["agent_alpha", "agent_beta", "agent_gamma"]

# Auto-load .env so the cron environment has DISCORD_BOT_TOKEN, etc.
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

DISCORD_ALERTS_CH = DISCORD_ALERTS_CH or os.environ.get("DISCORD_CHANNEL_ALERTS", "")

try:
    from _logger import setup as _logger_setup
    _logger_setup("weekly_synthesis")
except Exception:
    logging.basicConfig(level=logging.INFO)


def _last_monday(now: datetime) -> datetime:
    """Return Monday 00:00 ET of the current week. Delegates to _decision_helpers."""
    return this_week_monday(now)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=ET)
        return d
    except Exception:
        return None


def _load_journal_for_week(week_start: datetime, week_end: datetime) -> tuple[list, list]:
    """Return (closed_this_week, opened_this_week)."""
    closed, opened = [], []
    if not os.path.exists(DEFAULT_JOURNAL):
        return closed, opened
    with open(DEFAULT_JOURNAL) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                t = json.loads(raw)
            except Exception:
                continue
            ent = _parse_dt(t.get("timestamp"))
            cls = _parse_dt(t.get("close_timestamp") or t.get("exit_timestamp"))
            if ent and week_start <= ent <= week_end:
                opened.append(t)
            if cls and week_start <= cls <= week_end:
                closed.append(t)
    return closed, opened


def _load_files_in_dir(directory: Path, since: datetime) -> list[dict]:
    """Load JSON / MD files from a directory whose mtime is >= since.
    Returns [] if the directory can't be read (missing or permission denied).
    """
    out = []
    try:
        if not directory.exists():
            return out
    except PermissionError:
        return out
    cutoff = since.timestamp()
    try:
        entries = list(directory.iterdir())
    except (PermissionError, OSError):
        return out
    for p in entries:
        try:
            if not p.is_file():
                continue
            if p.stat().st_mtime < cutoff:
                continue
            if p.suffix == ".json":
                with open(p) as f:
                    out.append({"name": p.name, "data": json.load(f)})
            elif p.suffix == ".md":
                out.append({"name": p.name, "data": p.read_text()})
        except Exception as e:
            logging.warning("skip %s: %s", p, e)
    return out


def _load_agent_identity(agent: str) -> str:
    name_map = {
        "agent_alpha": "AGENT_ALPHA_IDENTITY.md",
        "agent_beta": "AGENT_BETA_IDENTITY.md",
        "agent_gamma": "AGENT_GAMMA_IDENTITY.md",
    }
    fp = REPO_AGENTS_DIR / name_map[agent]
    return fp.read_text() if fp.exists() else ""


def _aggregate_for_agent(agent: str, closed: list, opened: list, week_start: datetime) -> dict:
    a_closed = [t for t in closed if t.get("source") == agent]
    a_opened = [t for t in opened if t.get("source") == agent]
    diagnoses = _load_files_in_dir(REQUESTS_DIR / agent, week_start)
    reviews = _load_files_in_dir(REVIEWS_DIR / agent, week_start)

    pnls = [t.get("pnl") or 0 for t in a_closed]
    wins = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    win_rate = round(wins / len(pnls) * 100, 1) if pnls else 0
    return {
        "trades_closed": len(a_closed),
        "trades_opened": len(a_opened),
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "closed_trades": a_closed,
        "opened_trades": a_opened,
        "diagnoses": diagnoses,
        "reviews": reviews,
    }


SYSTEM_PROMPT = """You are the weekly synthesis engine for QuantAI's trading system. You produce the Friday capability report for {agent}.

Your output will be read by Amit during his Friday evening session. He will decide which upgrades to implement. Be direct, prioritized, evidence-based.

Your report has 5 sections:

1. PERFORMANCE SUMMARY — Trades this week, win rate, total P&L, notable outcomes.
2. CAPABILITY REQUESTS — Aggregated from trade diagnoses. Rank by (frequency × estimated_impact). Group related requests.
3. PARAMETER SUGGESTIONS — From trade reviews. Which strategy parameters to adjust based on this week's evidence?
4. KNOWLEDGE UPDATES — New learnings to add to the agent's identity file or skill files. Be specific about what to add and where.
5. INFRASTRUCTURE REQUESTS — Things requiring code changes, new data sources, or system mods. These can't be auto-applied — Amit builds them.

Use specific dollar amounts, percentages, and trade IDs as evidence. No vague recommendations.

If the agent had no activity, produce a brief note about why (no setups, circuit breaker, etc.) and whether that's expected or concerning.

Agent identity:
{identity}
"""


def _call_sonnet(system: str, user: str) -> str | None:
    try:
        from _llm_client import Client
        client = Client()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=LLM_TIMEOUT,
        )
        return resp.content[0].text
    except Exception as e:
        logging.warning("Sonnet call failed: %s", e)
        return None


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _summarize_for_discord(agent: str, week_start: datetime, agg: dict, synthesis_text: str) -> str:
    """Produce a Discord-sized summary (~1900 char limit)."""
    header = (
        f"📊 **Weekly Capability Report — {agent}**\n"
        f"Week of {week_start.strftime('%B %d, %Y')}\n\n"
    )
    perf = (
        f"**Performance:** {agg['trades_closed']} closed | "
        f"Win rate: {agg['win_rate']}% | "
        f"P&L: ${agg['total_pnl']}\n\n"
    )
    body = synthesis_text or "(no synthesis available — see full report)"
    out = header + perf + body
    return _truncate(out, 1900)


def _write_full_report(week_start: datetime, all_agg: dict,
                       all_syntheses: dict, dry_run: bool) -> Path | None:
    if dry_run:
        return None
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.warning("create reports dir failed: %s", e)
        return None
    fp = REPORTS_DIR / f"{week_start.strftime('%Y-%m-%d')}_synthesis.md"
    md = (
        f"# Weekly Synthesis Report — Week of {week_start.strftime('%Y-%m-%d')}\n\n"
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
    )
    total_trades = sum(a["trades_closed"] for a in all_agg.values())
    total_pnl = sum(a["total_pnl"] for a in all_agg.values())
    md += "## System Summary\n"
    md += f"- Total trades closed: {total_trades}\n"
    md += f"- Total P&L: ${total_pnl}\n\n"
    for agent in AGENTS:
        agg = all_agg.get(agent, {})
        md += f"---\n\n## {agent}\n\n"
        md += f"- Trades closed: {agg.get('trades_closed', 0)}\n"
        md += f"- Trades opened: {agg.get('trades_opened', 0)}\n"
        md += f"- Win rate: {agg.get('win_rate', 0)}%\n"
        md += f"- P&L: ${agg.get('total_pnl', 0)}\n\n"
        synth = all_syntheses.get(agent) or "(no synthesis — agent had no activity or LLM failed)"
        md += f"### Synthesis\n\n{synth}\n\n"
    try:
        fp.write_text(md)
        return fp
    except Exception as e:
        logging.warning("report write failed: %s", e)
        return None


def _post_discord(msg: str, dry_run: bool) -> bool:
    if dry_run:
        print("--- DRY RUN: would post to Discord ---")
        print(msg)
        return True
    if not DISCORD_ALERTS_CH:
        logging.warning("DISCORD_CHANNEL_ALERTS not set; skipping Discord post")
        return False
    try:
        from _discord import post_to_channel
        return post_to_channel(DISCORD_ALERTS_CH, msg)
    except Exception as e:
        logging.warning("Discord post failed: %s", e)
        return False


def synthesize(week_start: datetime, dry_run: bool = False) -> int:
    week_end = week_start + timedelta(days=7)
    closed, opened = _load_journal_for_week(week_start, week_end)
    print(f"[weekly_synthesis] week={week_start.date()} closed={len(closed)} opened={len(opened)}")

    all_agg = {}
    all_syntheses = {}

    for agent in AGENTS:
        agg = _aggregate_for_agent(agent, closed, opened, week_start)
        all_agg[agent] = agg

        if agg["trades_closed"] == 0 and agg["trades_opened"] == 0 and not agg["diagnoses"]:
            print(f"[weekly_synthesis] {agent}: no activity — skipping LLM call")
            all_syntheses[agent] = (
                "No activity this week. No setups triggered, or circuit breaker active. "
                "Expected when regime is unfavorable for the agent's strategy."
            )
            continue

        identity = _load_agent_identity(agent)
        identity_excerpt = identity[:2500] if identity else "(identity file not loaded)"

        # Bound the user-prompt size: trim trade fields and reviews
        compact_closed = [
            {
                "id": t.get("id"), "symbol": t.get("symbol"), "strategy": t.get("strategy"),
                "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
                "close_reason": t.get("close_reason") or t.get("exit_reason"),
                "thesis": (t.get("decision") or {}).get("thesis") or t.get("thesis"),
                "post_trade": t.get("post_trade"),
                "capability_diagnosis": t.get("capability_diagnosis"),
            }
            for t in agg["closed_trades"]
        ]

        user = (
            f"Produce the weekly synthesis for {agent}.\n\n"
            f"Week: {week_start.date()} to {week_end.date()}\n\n"
            f"PERFORMANCE: {agg['trades_closed']} closed, "
            f"{agg['trades_opened']} opened, "
            f"win rate {agg['win_rate']}%, P&L ${agg['total_pnl']}\n\n"
            f"CLOSED TRADES (compact):\n{json.dumps(compact_closed, indent=2, default=str)}\n\n"
            f"CAPABILITY DIAGNOSES THIS WEEK:\n"
            f"{json.dumps([d['data'] for d in agg['diagnoses']], indent=2, default=str)}\n\n"
            f"TRADE REVIEW MARKDOWN FILES (count={len(agg['reviews'])}):\n"
            + "\n".join((r.get('data', '') if isinstance(r.get('data'), str) else json.dumps(r.get('data')))
                        for r in agg['reviews'][:10])
            + "\n\nProduce the synthesis now. Be direct, evidence-based, prioritized."
        )
        # Hard cap user prompt to avoid context blowup
        user = _truncate(user, 30000)

        sys_prompt = SYSTEM_PROMPT.format(agent=agent, identity=identity_excerpt)

        if dry_run:
            print(f"--- DRY RUN: {agent} prompt size = {len(user)} chars ---")
            all_syntheses[agent] = "(dry-run synthesis placeholder)"
            continue

        text = _call_sonnet(sys_prompt, user)
        if not text:
            # Retry once
            text = _call_sonnet(sys_prompt, user)
        if not text:
            all_syntheses[agent] = (
                "Synthesis failed (Sonnet call returned no text after retry). "
                "Raw aggregated data is available in this report file."
            )
            continue
        all_syntheses[agent] = text

    # Write full report file
    fp = _write_full_report(week_start, all_agg, all_syntheses, dry_run)
    if fp:
        print(f"[weekly_synthesis] full report → {fp}")

    # Post per-agent summaries to Discord
    for agent in AGENTS:
        agg = all_agg[agent]
        synth = all_syntheses.get(agent) or ""
        msg = _summarize_for_discord(agent, week_start, agg, synth)
        ok = _post_discord(msg, dry_run)
        if not ok and not dry_run:
            logging.warning("Discord post failed for %s", agent)

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Weekly synthesis report.")
    p.add_argument("--dry-run", action="store_true",
                   help="No Discord post, no file write — print to stdout")
    p.add_argument("--week-start", help="ISO date for Monday of the week (default: this week's Monday)")
    args = p.parse_args()

    if args.week_start:
        try:
            week_start = datetime.fromisoformat(args.week_start).replace(tzinfo=ET)
        except Exception as e:
            print(f"bad --week-start: {e}")
            return 2
    else:
        week_start = _last_monday(datetime.now(timezone.utc))

    return synthesize(week_start, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
