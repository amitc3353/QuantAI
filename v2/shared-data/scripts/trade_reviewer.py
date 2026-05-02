#!/usr/bin/env python3
"""trade_reviewer — Haiku-driven post-trade thesis review at trade close.

Triggered inline by position_monitor.py after agent_self_diagnosis runs. Asks
an LLM to analyze what actually happened (was the thesis correct, was timing
right, what can be learned). Output feeds the Friday synthesis aggregation.

Failure must NEVER raise to the caller. Every error path logs and falls through.

CLI: python3 trade_reviewer.py --trade-id A015 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/trader/QuantAI/v2/shared-data/scripts")

from _journal_update import find_trade, update_trade_entry, DEFAULT_JOURNAL

REPO_AGENTS_DIR = Path("/home/trader/QuantAI/v2/shared-data/agents")
RUNTIME_REVIEWS_DIR = Path("/root/quantai-v2/shared-data/trade_reviews")
LLM_TIMEOUT = 20

try:
    from _logger import setup as _logger_setup
    _logger_setup("trade_reviewer")
except Exception:
    logging.basicConfig(level=logging.INFO)


def _load_agent_identity(agent_source: str) -> str:
    name_map = {
        "agent_alpha": "AGENT_ALPHA_IDENTITY.md",
        "agent_beta": "AGENT_BETA_IDENTITY.md",
        "agent_gamma": "AGENT_GAMMA_IDENTITY.md",
    }
    fname = name_map.get(agent_source)
    if not fname:
        return ""
    fp = REPO_AGENTS_DIR / fname
    if not fp.exists():
        return ""
    try:
        return fp.read_text()
    except Exception:
        return ""


def _holding_days(trade: dict) -> int | None:
    if trade.get("holding_days") is not None:
        try:
            return int(trade["holding_days"])
        except Exception:
            pass
    e = trade.get("timestamp")
    c = trade.get("close_timestamp") or trade.get("exit_timestamp")
    if not e or not c:
        return None
    try:
        e_dt = datetime.fromisoformat(str(e).replace("Z", "+00:00"))
        c_dt = datetime.fromisoformat(str(c).replace("Z", "+00:00"))
        return max(0, (c_dt - e_dt).days)
    except Exception:
        return None


SYSTEM_PROMPT = """You are the post-trade analyst for {agent_name}, an autonomous options trading agent.

Your job: analyze a just-closed trade and produce a structured review covering what happened, whether the thesis played out, and what can be learned.

Your output will be aggregated with other reviews in the Friday synthesis to identify patterns. Be concise and factual.

Agent identity (excerpt):
{agent_identity}

Respond with ONLY valid JSON (no surrounding prose, no markdown fences) in this exact shape:
{{
  "thesis_outcome": "confirmed|partially_confirmed|invalidated|inconclusive",
  "thesis_assessment": "<2-3 sentences: did the thesis play out? what actually happened?>",
  "regime_assessment": "<was the regime classification correct based on how the market actually behaved?>",
  "greeks_notes": "<theta decay rate, delta drift, gamma concerns; 'N/A' if not applicable>",
  "timing_assessment": "<was entry timing good? was exit timing good? would different timing have materially changed the outcome?>",
  "what_went_right": "<specific thing that worked, or null>",
  "what_went_wrong": "<specific thing that didn't work, or null>",
  "lessons": ["<specific lesson, max 2; empty array if nothing new>"],
  "parameter_suggestions": [
    {{
      "parameter": "<which parameter>",
      "current_value": "<what it is now>",
      "suggested_value": "<what it should be>",
      "reasoning": "<why, based on this trade>"
    }}
  ]
}}
"""


def _build_user_prompt(trade: dict) -> str:
    decision = trade.get("decision") or {}
    return (
        "Review this closed trade:\n\n"
        f"TRADE DATA:\n{json.dumps(trade, indent=2, default=str)}\n\n"
        "MARKET CONDITIONS:\n"
        f"- VIX at entry: {decision.get('vix_at_entry') or trade.get('vix_at_entry')}\n"
        f"- Regime at entry: {decision.get('regime_at_entry') or trade.get('regime_at_entry')}\n"
        f"- Holding days: {_holding_days(trade)}\n"
        f"- P&L: ${trade.get('pnl')} ({trade.get('pnl_pct')}%)\n"
        f"- Close reason: {trade.get('close_reason') or trade.get('exit_reason')}\n\n"
        f"ENTRY THESIS: {decision.get('thesis') or trade.get('thesis', 'Not recorded')}\n"
        f"ENTRY KEY RISK: {decision.get('key_risk', 'Not recorded')}\n"
        f"INVALIDATION CONDITION: {decision.get('invalidation') or trade.get('invalidation', 'Not recorded')}\n"
        f"CONVICTION AT ENTRY: {decision.get('conviction_score', 'N/A')}/10\n\n"
        "Produce the post-trade review as JSON only."
    )


def _call_haiku(system: str, user: str) -> str | None:
    try:
        from _llm_client import Client
        client = Client()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=LLM_TIMEOUT,
        )
        return resp.content[0].text
    except Exception as e:
        logging.warning("Haiku call failed: %s", e)
        return None


def _parse_json_response(text: str) -> dict | None:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        return json.loads(s)
    except Exception:
        first, last = s.find("{"), s.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(s[first:last + 1])
            except Exception:
                return None
        return None


def _write_review_md(agent_source: str, trade_id: str, review: dict) -> None:
    agent_dir = RUNTIME_REVIEWS_DIR / agent_source
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    fp = agent_dir / f"{trade_id}.md"
    md = (
        f"# Trade Review: {trade_id}\n"
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"**Agent:** {agent_source}\n\n"
        f"## Thesis Outcome: {review.get('thesis_outcome', 'unknown')}\n"
        f"{review.get('thesis_assessment', '')}\n\n"
        f"## Regime Assessment\n{review.get('regime_assessment', '')}\n\n"
        f"## Greeks Notes\n{review.get('greeks_notes', 'N/A')}\n\n"
        f"## Timing Assessment\n{review.get('timing_assessment', '')}\n\n"
        f"## What Went Right\n{review.get('what_went_right') or 'Nothing notable'}\n\n"
        f"## What Went Wrong\n{review.get('what_went_wrong') or 'Nothing notable'}\n\n"
        "## Lessons\n"
    )
    lessons = review.get("lessons") or []
    if lessons:
        for l in lessons:
            md += f"- {l}\n"
    else:
        md += "- No new lessons from this trade.\n"
    sugs = review.get("parameter_suggestions") or []
    if sugs:
        md += "\n## Parameter Suggestions\n"
        for s in sugs:
            md += (
                f"- **{s.get('parameter', '?')}:** "
                f"{s.get('current_value', '?')} → {s.get('suggested_value', '?')} — "
                f"{s.get('reasoning', '')}\n"
            )
    try:
        with open(fp, "w") as f:
            f.write(md)
    except Exception as e:
        logging.warning("review markdown write failed: %s", e)


def review(trade_id: str, dry_run: bool = False) -> dict | None:
    """Run post-trade review. Never raises."""
    try:
        trade = find_trade(trade_id)
        if not trade:
            logging.warning("review: trade %s not found", trade_id)
            return None

        agent_source = trade.get("source", "")
        identity = _load_agent_identity(agent_source)
        identity_excerpt = identity[:1500] if identity else "(identity file not loaded)"

        system_prompt = SYSTEM_PROMPT.format(
            agent_name=agent_source, agent_identity=identity_excerpt,
        )
        user_prompt = _build_user_prompt(trade)

        if dry_run:
            print("--- DRY RUN: would call Haiku with ---")
            print("SYSTEM:", system_prompt[:400], "...")
            print("USER:", user_prompt[:600], "...")
            return {"dry_run": True, "context_size": len(user_prompt)}

        text = _call_haiku(system_prompt, user_prompt)
        if not text:
            logging.warning("review: empty Haiku response for %s", trade_id)
            return None

        review_data = _parse_json_response(text)
        if not review_data:
            logging.warning("review: unparseable response for %s: %s", trade_id, text[:200])
            return None

        update_trade_entry(trade_id, {"post_trade": review_data})
        _write_review_md(agent_source, trade_id, review_data)
        logging.info("review: %s — outcome=%s", trade_id, review_data.get("thesis_outcome"))
        return review_data

    except Exception as e:
        logging.exception("review: unexpected error for %s: %s", trade_id, e)
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="Post-trade review of a closed trade.")
    p.add_argument("--trade-id", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    out = review(args.trade_id, dry_run=args.dry_run)
    if out is None and not args.dry_run:
        print(f"review: no result for {args.trade_id}")
        return 1
    if out is not None:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
