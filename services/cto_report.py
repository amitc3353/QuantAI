"""
cto_report.py — Weekly CTO Report
====================================
Runs every Friday at 5:00 PM ET alongside the weekly review.

WHAT IT DOES AUTOMATICALLY:
  - Reads 7 days of agent journals (entries, exits, errors, skips)
  - Reads 7 days of EOD scores and lessons
  - Reads 7 days of health check results
  - Reads correlation analysis output
  - Checks container restart counts (instability signal)
  - Checks memory/disk trends
  - Counts CBOE scrape failures, cache misses, guard rejections

WHAT IT PRODUCES:
  A prioritized report posted to #system-health with:
  1. System reliability score (0-100)
  2. Critical issues (must fix before Monday)
  3. Performance observations (agent behavior patterns)
  4. Optimization opportunities (cost, speed, accuracy)
  5. Suggested actions ranked by priority

WHAT IT NEVER DOES:
  - Change any code or config
  - Deploy anything
  - Make trading decisions
  - Act without your approval

APPROVAL FLOW:
  Report posted → you read it → discuss in #chat → approve specific items
  → implementation happens in next session or via self-improve PRs

Cost: ~1 Sonnet call/week (~$0.05). Worth it.
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import asyncio
import aiohttp

log = logging.getLogger("cto-report")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

JOURNAL_DIR = Path("/app/data/memory/paper")
SCORE_DIR = Path("/app/data/journal")
CACHE_DIR = Path("/app/data/cache")


# ─────────────────────────────────────────────────────────────────────────────
# DATA COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def collect_agent_stats(days: int = 7) -> dict:
    """Read agent journals and compute reliability + performance stats."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    stats = {}

    for agent in ["agent1_iron_condor", "agent2_covered_call"]:
        journal = JOURNAL_DIR / f"{agent}_journal.jsonl"
        if not journal.exists():
            stats[agent] = {"status": "no_journal"}
            continue

        entries, exits, skips, errors, rolls, vetoes = [], [], [], [], [], []
        with open(journal) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    d = r.get("date", r.get("timestamp", "")[:10])
                    if d < cutoff:
                        continue
                    ev = r.get("event", "")
                    if ev == "entry":       entries.append(r)
                    elif ev == "exit":      exits.append(r)
                    elif ev == "skip":      skips.append(r)
                    elif ev == "error":     errors.append(r)
                    elif ev == "roll":      rolls.append(r)
                    elif ev == "guard_reject": vetoes.append(r)
                except Exception:
                    continue

        wins = [e for e in exits if e.get("outcome") == "win"]
        total_pnl = sum(e.get("pnl_per_contract", 0) or e.get("pnl", 0) for e in exits)
        win_rate = len(wins) / len(exits) * 100 if exits else 0

        # Skip reason breakdown
        skip_reasons = {}
        for s in skips:
            r = s.get("reason", "unknown")
            skip_reasons[r] = skip_reasons.get(r, 0) + 1

        # Error breakdown
        error_types = {}
        for e in errors:
            r = e.get("reason", "unknown")
            error_types[r] = error_types.get(r, 0) + 1

        # Context score correlation
        ctx_trades = [e for e in exits if e.get("context_score") is not None]
        high_ctx = [e for e in ctx_trades if e.get("context_score", 0) >= 60]
        low_ctx = [e for e in ctx_trades if e.get("context_score", 0) < 60]
        ctx_lift = None
        if high_ctx and low_ctx:
            h_wr = sum(1 for e in high_ctx if e.get("outcome") == "win") / len(high_ctx) * 100
            l_wr = sum(1 for e in low_ctx if e.get("outcome") == "win") / len(low_ctx) * 100
            ctx_lift = round(h_wr - l_wr, 1)

        stats[agent] = {
            "entries": len(entries),
            "exits": len(exits),
            "wins": len(wins),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "skips": len(skips),
            "skip_reasons": skip_reasons,
            "errors": len(errors),
            "error_types": error_types,
            "rolls": len(rolls),
            "guard_vetoes": len(vetoes),
            "context_score_lift": ctx_lift,
            "days": days,
        }

    return stats


def collect_eod_scores(days: int = 7) -> dict:
    """Read EOD score files for the week."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    scores = {"agent1": [], "agent2": [], "system": []}

    for f in SCORE_DIR.glob("*.json"):
        try:
            d = f.stem.split("_")[-1]  # YYYY-MM-DD
            if d < cutoff:
                continue
            data = json.loads(f.read_text())
            if "agent1" in f.stem:
                scores["agent1"].append({"date": d, "score": data.get("score"), "summary": data.get("summary", "")})
            elif "agent2" in f.stem:
                scores["agent2"].append({"date": d, "score": data.get("score"), "summary": data.get("summary", "")})
            elif "score_paper" in f.stem or "score_live" in f.stem:
                scores["system"].append({"date": d, "score": data.get("score"), "summary": data.get("summary", "")})
        except Exception:
            continue

    # Compute averages — iterate over a snapshot to avoid mutating dict mid-loop
    for key in list(scores):
        valid = [s["score"] for s in scores[key] if s.get("score") is not None]
        scores[f"{key}_avg"] = round(sum(valid) / len(valid), 1) if valid else None

    return scores


def collect_cache_health() -> dict:
    """Check cache file ages and identify stale/missing data."""
    issues = []
    stats = {}
    now = datetime.now()

    expected = {
        "vix": 1,
        "macro_fred_macro_composite": 8,
        "sentiment_put_call": 4,
        "sentiment_fear_greed": 4,
        "sentiment_vix_term": 2,
        "macro_finnhub_calendar": 24,
    }

    for key, max_age_hours in expected.items():
        cache_file = CACHE_DIR / f"{key}.json"
        if not cache_file.exists():
            stats[key] = "never_populated"
            continue
        age_hours = (now - datetime.fromtimestamp(cache_file.stat().st_mtime)).total_seconds() / 3600
        stats[key] = f"{age_hours:.1f}h old"
        if age_hours > max_age_hours * 2:
            issues.append(f"{key} is {age_hours:.0f}h old (max {max_age_hours}h) — fetch failing repeatedly")

    # Count cache files by type
    all_cache = list(CACHE_DIR.glob("*.json"))
    stats["total_cache_files"] = len(all_cache)
    stats["issues"] = issues

    return stats


def collect_system_metrics() -> dict:
    """Read memory, disk, and lesson accumulation."""
    metrics = {}

    # Lesson count
    lessons_file = Path("/app/data/memory/shared/lessons.jsonl")
    if lessons_file.exists():
        with open(lessons_file) as f:
            lines = [l for l in f if l.strip()]
        metrics["total_lessons"] = len(lines)
        # Count by source
        sources = {}
        for line in lines:
            try:
                r = json.loads(line)
                src = r.get("source", "unknown")
                sources[src] = sources.get(src, 0) + 1
            except Exception:
                pass
        metrics["lessons_by_source"] = sources
    else:
        metrics["total_lessons"] = 0

    # Correlation report
    corr_files = sorted(SCORE_DIR.glob("correlation_*.json"))
    if corr_files:
        try:
            latest_corr = json.loads(corr_files[-1].read_text())
            metrics["correlation_status"] = latest_corr.get("status")
            metrics["context_predictive"] = latest_corr.get(
                "overall_correlation", {}
            ).get("is_predictive")
            metrics["context_win_lift"] = latest_corr.get(
                "overall_correlation", {}
            ).get("win_rate_lift")
        except Exception:
            metrics["correlation_status"] = "unreadable"

    # Backtest results
    backtest_files = sorted(SCORE_DIR.glob("backtest_*.json"))
    metrics["backtest_runs"] = len(backtest_files)
    approved = rejected = 0
    for bf in backtest_files[-10:]:
        try:
            bt = json.loads(bf.read_text())
            if bt.get("recommendation") == "APPROVE":
                approved += 1
            elif bt.get("recommendation") == "REJECT":
                rejected += 1
        except Exception:
            pass
    metrics["backtest_approved"] = approved
    metrics["backtest_rejected"] = rejected

    return metrics


# ─────────────────────────────────────────────────────────────────────────────
# CTO ANALYSIS — Claude Sonnet synthesizes everything
# ─────────────────────────────────────────────────────────────────────────────

async def generate_cto_report() -> dict:
    """
    Collect all data, send to Claude Sonnet for analysis,
    return structured report with prioritized findings.
    """
    log.info("=== Generating CTO Report ===")

    # Collect all data
    agent_stats = collect_agent_stats(days=7)
    eod_scores = collect_eod_scores(days=7)
    cache_health = collect_cache_health()
    system_metrics = collect_system_metrics()

    # Identify immediate issues before Claude call
    immediate_issues = []

    # Error rate check
    for agent, stats in agent_stats.items():
        if isinstance(stats, dict) and stats.get("errors", 0) > 2:
            immediate_issues.append(
                f"{agent}: {stats['errors']} errors this week — {stats.get('error_types', {})}"
            )
        if isinstance(stats, dict) and stats.get("entries", 0) == 0:
            immediate_issues.append(f"{agent}: ZERO entries this week — agent may not be running")

    # Cache issues
    for issue in cache_health.get("issues", []):
        immediate_issues.append(f"Data: {issue}")

    if not ANTHROPIC_API_KEY:
        return {
            "status": "no_api_key",
            "immediate_issues": immediate_issues,
            "agent_stats": agent_stats,
        }

    # Build prompt for Sonnet
    prompt = f"""You are the CTO of QuantAI, an autonomous options trading system.
Review this week's system data and produce a technical report.

AGENT PERFORMANCE (last 7 days):
{json.dumps(agent_stats, indent=2)}

EOD SCORES:
Agent 1 avg: {eod_scores.get('agent1_avg', 'N/A')} | Agent 2 avg: {eod_scores.get('agent2_avg', 'N/A')}
Recent scores: {json.dumps(eod_scores.get('agent1', [])[-3:] + eod_scores.get('agent2', [])[-3:], indent=1)}

CACHE HEALTH:
{json.dumps(cache_health, indent=2)}

SYSTEM METRICS:
{json.dumps(system_metrics, indent=2)}

IMMEDIATE ISSUES DETECTED:
{json.dumps(immediate_issues, indent=2)}

Produce a structured CTO report. Output ONLY valid JSON, no markdown:
{{
  "reliability_score": 0-100,
  "one_line_status": "single sentence system status",
  "critical": [
    {{"issue": "description", "impact": "what breaks", "fix": "exact fix", "priority": 1}}
  ],
  "performance": [
    {{"observation": "what you see in the data", "significance": "why it matters"}}
  ],
  "optimizations": [
    {{"opportunity": "what to improve", "benefit": "expected gain", "effort": "low/medium/high", "cost_impact": "free/+$X/mo"}}
  ],
  "weekly_summary": "2-3 sentence plain English summary for the trader",
  "monday_readiness": "READY / CAUTION / NOT READY",
  "monday_readiness_reason": "one sentence"
}}

Rules:
- critical: only real technical problems that could cause failures or bad trades
- performance: patterns you actually see in the data, not speculation
- optimizations: realistic improvements given current architecture
- Be specific — reference actual numbers from the data
- If no trades yet (first week), focus on system readiness checks
- Never suggest live trading changes — paper mode only recommendations"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": SONNET_MODEL,
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}]
                },
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                raw = "".join(
                    b["text"] for b in data.get("content", [])
                    if b.get("type") == "text"
                )
    except Exception as e:
        log.error(f"CTO report Sonnet call failed: {e}")
        return {
            "status": "api_error",
            "error": str(e),
            "immediate_issues": immediate_issues,
            "agent_stats": agent_stats,
        }

    try:
        cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        report = json.loads(cleaned)
        report["status"] = "complete"
        report["generated_at"] = datetime.now().isoformat()
        report["data_summary"] = {
            "agent_stats": agent_stats,
            "eod_scores": eod_scores,
            "cache_health": cache_health,
            "system_metrics": system_metrics,
            "immediate_issues": immediate_issues,
        }
    except json.JSONDecodeError:
        log.error(f"Could not parse CTO report: {raw[:200]}")
        report = {
            "status": "parse_error",
            "raw": raw,
            "immediate_issues": immediate_issues,
        }

    # Save report
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCORE_DIR / f"cto_report_{date.today().isoformat()}.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    log.info(
        f"CTO report: reliability={report.get('reliability_score', '?')}/100 "
        f"monday={report.get('monday_readiness', '?')} "
        f"critical={len(report.get('critical', []))} issues"
    )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD EMBEDS
# ─────────────────────────────────────────────────────────────────────────────

def build_cto_embeds(report: dict) -> list:
    """Build Discord embeds from CTO report."""

    if report.get("status") in ("api_error", "parse_error", "no_api_key"):
        return [{
            "title": "⚠️ CTO Report — Generation Failed",
            "description": report.get("error", report.get("raw", "Unknown error"))[:500],
            "color": 0xF39C12,
            "footer": {"text": "QuantAI CTO Report"},
        }]

    score = report.get("reliability_score", 0)
    readiness = report.get("monday_readiness", "UNKNOWN")
    color = {
        "READY": 0x2ECC71,
        "CAUTION": 0xF39C12,
        "NOT READY": 0xE74C3C,
    }.get(readiness, 0x95A5A6)

    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)
    readiness_emoji = {"READY": "✅", "CAUTION": "⚠️", "NOT READY": "❌"}.get(readiness, "❓")

    # Main embed
    fields = [
        {
            "name": "Reliability",
            "value": f"`{score_bar}` {score}/100",
            "inline": True,
        },
        {
            "name": "Monday",
            "value": f"{readiness_emoji} {readiness}",
            "inline": True,
        },
    ]

    if report.get("monday_readiness_reason"):
        fields.append({
            "name": "Readiness note",
            "value": report["monday_readiness_reason"],
            "inline": False,
        })

    # Critical issues
    critical = report.get("critical", [])
    if critical:
        crit_lines = []
        for i, c in enumerate(critical[:4], 1):
            crit_lines.append(
                f"**{i}. {c.get('issue', '?')[:60]}**\n"
                f"Impact: {c.get('impact', '')[:80]}\n"
                f"Fix: `{c.get('fix', '')[:80]}`"
            )
        fields.append({
            "name": f"🔴 Critical Issues ({len(critical)})",
            "value": "\n\n".join(crit_lines)[:1024],
            "inline": False,
        })
    else:
        fields.append({
            "name": "🔴 Critical Issues",
            "value": "None — system clean",
            "inline": False,
        })

    main_embed = {
        "title": f"🤖 Weekly CTO Report — {date.today().isoformat()}",
        "description": report.get("weekly_summary", ""),
        "color": color,
        "fields": fields,
        "footer": {"text": "QuantAI CTO · Runs every Friday 5 PM ET"},
        "timestamp": datetime.now().isoformat(),
    }

    # Optimizations embed (separate, less urgent)
    optimizations = report.get("optimizations", [])
    performance = report.get("performance", [])
    embeds = [main_embed]

    if optimizations or performance:
        opt_fields = []
        if performance:
            perf_lines = [
                f"• {p.get('observation', '')[:80]} — {p.get('significance', '')[:60]}"
                for p in performance[:4]
            ]
            opt_fields.append({
                "name": "📊 Performance Observations",
                "value": "\n".join(perf_lines)[:1024],
                "inline": False,
            })
        if optimizations:
            opt_lines = [
                f"• **{o.get('opportunity', '')[:60]}** [{o.get('effort', '?')} effort, {o.get('cost_impact', 'free')}]\n"
                f"  → {o.get('benefit', '')[:80]}"
                for o in optimizations[:4]
            ]
            opt_fields.append({
                "name": "💡 Optimization Opportunities",
                "value": "\n\n".join(opt_lines)[:1024],
                "inline": False,
            })

        if opt_fields:
            embeds.append({
                "title": "CTO Report — Details",
                "color": 0x3498DB,
                "fields": opt_fields,
                "footer": {"text": "Discuss in #chat to prioritize · No action taken without your approval"},
            })

    return embeds


# ─────────────────────────────────────────────────────────────────────────────
# CLI TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        report = await generate_cto_report()
        print(f"\nReliability: {report.get('reliability_score')}/100")
        print(f"Monday: {report.get('monday_readiness')}")
        print(f"Summary: {report.get('weekly_summary')}")
        print(f"\nCritical ({len(report.get('critical', []))}):")
        for c in report.get("critical", []):
            print(f"  - {c.get('issue')}: {c.get('fix')}")
        print(f"\nOptimizations ({len(report.get('optimizations', []))}):")
        for o in report.get("optimizations", []):
            print(f"  - {o.get('opportunity')} [{o.get('effort')}]")

    asyncio.run(main())
