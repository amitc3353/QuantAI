#!/usr/bin/env python3
"""agent_self_diagnosis — Haiku-driven capability gap analysis at trade close.

Triggered inline by position_monitor.py after a trade closes. Asks an LLM to
identify what the agent was MISSING (data, speed, knowledge, strategy, analysis)
that would have changed the decision or improved the outcome.

This is NOT a trade review — that's trade_reviewer.py. This is "what does the
agent need to do better next time?"

Failure must NEVER raise to the caller. Every error path falls through to
writing capability_diagnosis=null and logging.

CLI: python3 agent_self_diagnosis.py --trade-id A015 [--dry-run]
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
RUNTIME_REQUESTS_DIR = Path("/root/quantai-v2/shared-data/capability_requests")
LLM_TIMEOUT = 20  # seconds — bound the inline call from position_monitor

# Logger setup mirrors the rest of the codebase
try:
    from _logger import setup as _logger_setup
    _logger_setup("agent_self_diagnosis")
except Exception:
    logging.basicConfig(level=logging.INFO)


def _load_agent_identity(agent_source: str) -> str:
    """Load AGENT_*_IDENTITY.md content. Returns empty string if missing."""
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


def _concurrent_positions(trade_entry: dict, journal_path: str = DEFAULT_JOURNAL) -> list[dict]:
    """Find trades that were OPEN overlapping with this trade's lifetime."""
    if not os.path.exists(journal_path):
        return []
    entry_ts = trade_entry.get("timestamp")
    close_ts = trade_entry.get("close_timestamp") or trade_entry.get("exit_timestamp")
    if not entry_ts or not close_ts:
        return []
    try:
        e_dt = datetime.fromisoformat(str(entry_ts).replace("Z", "+00:00"))
        c_dt = datetime.fromisoformat(str(close_ts).replace("Z", "+00:00"))
    except Exception:
        return []
    out = []
    with open(journal_path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                t = json.loads(raw)
            except Exception:
                continue
            if t.get("id") == trade_entry.get("id"):
                continue
            t_e = t.get("timestamp")
            t_c = t.get("close_timestamp") or t.get("exit_timestamp") or "9999-12-31T00:00:00+00:00"
            try:
                t_e_dt = datetime.fromisoformat(str(t_e).replace("Z", "+00:00"))
                t_c_dt = datetime.fromisoformat(str(t_c).replace("Z", "+00:00"))
            except Exception:
                continue
            # Overlap: started before this trade closed AND ended after this trade opened
            if t_e_dt < c_dt and t_c_dt > e_dt:
                out.append({
                    "id": t.get("id"),
                    "source": t.get("source"),
                    "symbol": t.get("symbol"),
                    "strategy": t.get("strategy"),
                })
    return out


def _build_context(trade_entry: dict) -> dict:
    decision = trade_entry.get("decision") or {}
    post = trade_entry.get("post_trade") or {}
    return {
        "trade_id": trade_entry.get("id"),
        "agent": trade_entry.get("source"),
        "symbol": trade_entry.get("symbol"),
        "strategy": trade_entry.get("strategy"),
        "entry_time": trade_entry.get("timestamp"),
        "close_time": trade_entry.get("close_timestamp") or trade_entry.get("exit_timestamp"),
        "close_reason": trade_entry.get("close_reason") or trade_entry.get("exit_reason"),
        "pnl": trade_entry.get("pnl"),
        "pnl_pct": trade_entry.get("pnl_pct"),
        "thesis": decision.get("thesis") or trade_entry.get("thesis", "Not recorded"),
        "key_risk": decision.get("key_risk", "Not recorded"),
        "invalidation": decision.get("invalidation") or trade_entry.get("invalidation", "Not recorded"),
        "conviction_score": decision.get("conviction_score"),
        "regime_at_entry": decision.get("regime_at_entry") or trade_entry.get("regime_at_entry"),
        "vix_at_entry": decision.get("vix_at_entry") or trade_entry.get("vix_at_entry"),
        "regime_at_close": post.get("regime_at_close"),
        "vix_at_close": post.get("vix_at_close"),
        "vix_data_age_seconds": decision.get("vix_data_age_seconds"),
        "chain_data_age_seconds": decision.get("chain_data_age_seconds"),
        "market_intel_age_seconds": decision.get("market_intel_age_seconds"),
        "pipeline_durations": decision.get("pipeline_stage_durations"),
        "estimated_credit_or_debit": (
            trade_entry.get("estimated_credit") or trade_entry.get("net_debit")
            or trade_entry.get("net_credit")
        ),
        "actual_fill": trade_entry.get("avg_fill_price"),
        "holding_days": trade_entry.get("holding_days"),
    }


SYSTEM_PROMPT = """You are the self-diagnosis module for {agent_name}, an autonomous options trading agent.

Your job: analyze a just-closed trade and identify specific CAPABILITY GAPS — things the agent was missing (data, speed, knowledge, strategy, analysis) that would have changed the decision or improved the outcome.

You are NOT writing a trade review. You are diagnosing what the agent NEEDS to do better next time.

IMPORTANT RULES:
1. Be specific. "Need better data" is useless. "Need VIX refresh every 5 minutes instead of 90-minute cache" is actionable.
2. Only flag gaps that would have materially changed the outcome. A $0.01 slippage on a $180 winner is not a gap worth flagging.
3. If the trade went well and nothing was missing, say so explicitly. Not every trade has a gap.
4. Consider all 6 dimensions: data_freshness, data_coverage, execution_timing, analytical_depth, strategy_gaps, knowledge_gaps.
5. Maximum 3 gaps per trade. If you find more, pick the 3 most impactful.
6. Estimate dollar impact where possible. "Would have avoided this $180 loss" or "Would have improved fill by ~$0.10 ($10 per contract)".

Agent identity (excerpt):
{agent_identity}

Respond with ONLY valid JSON (no surrounding prose, no markdown fences) in this exact shape:
{{
  "gaps_identified": [
    {{
      "dimension": "data_freshness|data_coverage|execution_timing|analytical_depth|strategy_gaps|knowledge_gaps",
      "request": "<specific, actionable capability request>",
      "evidence": "<what happened that exposed this gap>",
      "priority": "critical|would_help|nice_to_have",
      "estimated_impact_dollars": <number or null>
    }}
  ],
  "no_gaps_note": "<if gaps_identified is empty, explain why no gaps exist; null otherwise>"
}}
"""


def _call_haiku(system: str, user: str) -> str | None:
    """Call Haiku with a tight timeout. Returns text or None on failure."""
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
    # Strip optional markdown fences
    if s.startswith("```"):
        s = s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        data = json.loads(s)
    except Exception:
        # Try to find a JSON object in the response
        first = s.find("{")
        last = s.rfind("}")
        if first >= 0 and last > first:
            try:
                data = json.loads(s[first:last + 1])
            except Exception:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    if "gaps_identified" not in data:
        data["gaps_identified"] = []
    if "no_gaps_note" not in data:
        data["no_gaps_note"] = None
    return data


def _write_standalone_file(agent_source: str, trade_id: str, diagnosis: dict) -> None:
    agent_dir = RUNTIME_REQUESTS_DIR / agent_source
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    fp = agent_dir / f"{trade_id}.json"
    try:
        with open(fp, "w") as f:
            json.dump({
                "trade_id": trade_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "diagnosis": diagnosis,
            }, f, indent=2)
    except Exception as e:
        logging.warning("standalone diagnosis write failed: %s", e)


def diagnose(trade_id: str, dry_run: bool = False) -> dict | None:
    """Run capability diagnosis for a closed trade. Never raises."""
    try:
        trade = find_trade(trade_id)
        if not trade:
            logging.warning("diagnose: trade %s not found", trade_id)
            return None

        agent_source = trade.get("source", "")
        identity = _load_agent_identity(agent_source)
        # Trim identity to ~1500 chars to bound prompt size
        identity_excerpt = identity[:1500] if identity else "(identity file not loaded)"

        ctx = _build_context(trade)
        concurrent = _concurrent_positions(trade)

        user_prompt = (
            "Trade just closed. Analyze for capability gaps.\n\n"
            "TRADE DATA:\n"
            f"{json.dumps(ctx, indent=2, default=str)}\n\n"
            "CONCURRENT POSITIONS (open at same time):\n"
            f"{json.dumps(concurrent, indent=2, default=str)}\n\n"
            "What capability gaps, if any, would have changed this trade's decision or outcome?\n"
            "Be specific, actionable, and don't flag minor issues on winning trades."
        )

        system_prompt = SYSTEM_PROMPT.format(
            agent_name=agent_source,
            agent_identity=identity_excerpt,
        )

        if dry_run:
            print("--- DRY RUN: would call Haiku with ---")
            print("SYSTEM:", system_prompt[:400], "...")
            print("USER:", user_prompt[:600], "...")
            return {"dry_run": True, "context_size": len(user_prompt)}

        text = _call_haiku(system_prompt, user_prompt)
        if not text:
            logging.warning("diagnose: empty Haiku response for %s", trade_id)
            update_trade_entry(trade_id, {"capability_diagnosis": None})
            return None

        diagnosis = _parse_json_response(text)
        if not diagnosis:
            logging.warning("diagnose: unparseable response for %s: %s", trade_id, text[:200])
            update_trade_entry(trade_id, {"capability_diagnosis": None})
            return None

        update_trade_entry(trade_id, {"capability_diagnosis": diagnosis})
        _write_standalone_file(agent_source, trade_id, diagnosis)
        n_gaps = len(diagnosis.get("gaps_identified") or [])
        logging.info("diagnose: %s — %d gaps identified", trade_id, n_gaps)
        return diagnosis

    except Exception as e:
        logging.exception("diagnose: unexpected error for %s: %s", trade_id, e)
        try:
            update_trade_entry(trade_id, {"capability_diagnosis": None})
        except Exception:
            pass
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="Self-diagnose a closed trade.")
    p.add_argument("--trade-id", required=True, help="Journal entry id (A###/B###/G###)")
    p.add_argument("--dry-run", action="store_true", help="Build prompt but don't call LLM or write")
    args = p.parse_args()
    out = diagnose(args.trade_id, dry_run=args.dry_run)
    if out is None and not args.dry_run:
        print(f"diagnose: no result for {args.trade_id}")
        return 1
    if out is not None:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
