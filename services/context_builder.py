"""
context_builder.py — Pre-Trade Context Score (0–100)
======================================================
The brain. Assembles ALL signals into one actionable score before every entry.

SCORING MODEL (100 points total):
  VIX regime        25 pts  — current volatility environment
  Event calendar    15 pts  — FOMC/CPI/earnings proximity
  Macro regime      20 pts  — yield curve, Fed rate, CPI environment
  Sentiment         20 pts  — put/call ratio + Fear & Greed
  Flow              20 pts  — Vol/OI unusual activity + dark pool proxy

SCORE INTERPRETATION:
  ≥ 60 → PROCEED    Standard parameters
  40–59 → CAUTION   Widen Agent 1 wings +$2, raise Agent 2 delta to 0.15 min
  < 40 → SKIP       Post reason to Discord, do not enter

RUNS:
  6:30 AM  — morning brief enrichment (Sonnet uses it for analysis)
  9:45 AM  — pre-Agent 1 entry 1
  11:25 AM — pre-Agent 1 entry 2
  Monday 9:50 AM — pre-Agent 2 weekly scan

COST: ~500 tokens per call to Haiku = ~$0.04/day added cost
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio
import sys

sys.path.insert(0, "/app/services")

log = logging.getLogger("context-builder")

CACHE_DIR = Path("/app/data/cache")
CACHE_TTL_MINUTES = 20  # Short cache — context is time-sensitive


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"context_{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        age_min = (datetime.now() - cached_at).total_seconds() / 60
        if age_min < CACHE_TTL_MINUTES:
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    with open(_cache_path(key), "w") as f:
        json.dump(data, f, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# SCORING FUNCTIONS — Each returns (points_earned, max_points, explanation)
# ─────────────────────────────────────────────────────────────────────────────

def score_vix(vix_data: dict) -> tuple:
    """VIX regime score — 25 points max."""
    vix = vix_data.get("vix", 0)
    regime = vix_data.get("regime", "unknown")
    term = vix_data.get("term_shape", "unknown")

    # SAFETY: if VIX is 0 or missing, data is bad — don't score high
    if vix <= 0 or vix_data.get("_error"):
        pts = 0
        note = f"VIX data unavailable or invalid (vix={vix}) — scoring 0"
        return pts, 25, note

    if regime == "halt" or vix >= 35:
        pts = 0
        note = f"VIX {vix:.1f} — system halt territory"
    elif regime == "danger" or vix >= 30:
        pts = 5
        note = f"VIX {vix:.1f} — danger zone, advisory only"
    elif regime == "high" or vix >= 25:
        pts = 12
        note = f"VIX {vix:.1f} — elevated, widen wings"
    elif regime == "elevated" or vix >= 18:
        pts = 20
        note = f"VIX {vix:.1f} — elevated but tradeable, good premium"
    elif regime == "normal" or 13 <= vix < 18:
        pts = 25
        note = f"VIX {vix:.1f} — ideal conditions"
    elif regime == "extremely_low" or vix < 13:
        pts = 10
        note = f"VIX {vix:.1f} — too low, premiums too cheap"
    else:
        pts = 15
        note = f"VIX {vix:.1f} — unknown regime"

    # VIX term structure modifier
    if term == "backwardation":
        pts = max(0, pts - 10)
        note += " | Term: backwardation (acute stress)"
    elif term == "near_term_spike":
        pts = max(0, pts - 7)
        note += " | Term: near-term spike"
    elif term == "contango":
        note += " | Term: contango (normal)"

    return pts, 25, note


def score_event_calendar(macro_context: dict) -> tuple:
    """Event calendar score — 15 points max."""
    days_away = macro_context.get("days_to_next_event", 99)
    is_event_today = macro_context.get("is_event_today", False)
    next_event = macro_context.get("next_event")
    event_name = next_event.get("name", "unknown event") if next_event else "none"

    if is_event_today:
        pts = 0
        note = f"EVENT TODAY: {event_name} — skip all entries"
    elif days_away <= 1:
        pts = 2
        note = f"Event tomorrow: {event_name} — high caution"
    elif days_away <= 3:
        pts = 6
        note = f"Event in {days_away} days: {event_name} — widen wings"
    elif days_away <= 7:
        pts = 10
        note = f"Event in {days_away} days: {event_name} — note it, proceed"
    else:
        pts = 15
        note = f"No major events within 7 days — clear calendar"

    return pts, 15, note


def score_macro(macro_context: dict) -> tuple:
    """Macro regime score — 20 points max."""
    fred = macro_context.get("fred", {})
    macro_stress = fred.get("macro_stress_score", 50)
    yield_curve = fred.get("yield_curve_regime", "unknown")
    macro_regime = fred.get("macro_regime", "unknown")

    if macro_regime == "stressed":
        pts = 5
        note = f"Macro stressed: yield curve={yield_curve}, stress score={macro_stress}"
    elif macro_regime == "cautious":
        pts = 12
        note = f"Macro cautious: yield curve={yield_curve}"
    elif macro_regime == "healthy":
        pts = 20
        note = f"Macro healthy: yield curve={yield_curve}"
    else:
        pts = 10
        note = f"Macro unknown — defaulting to partial score"

    return pts, 20, note


def score_sentiment(sentiment_context: dict) -> tuple:
    """Sentiment score — 20 points max."""
    pcr = sentiment_context.get("put_call_ratio", {})
    fg = sentiment_context.get("fear_greed", {})

    pcr_regime = pcr.get("pcr_regime", "unknown")
    fg_regime = fg.get("regime", "unknown")
    pcr_value = pcr.get("total_pcr") or pcr.get("equity_pcr")
    fg_score = fg.get("score", 50)

    # PCR scoring (10 points)
    if pcr_regime == "neutral":
        pcr_pts = 10
    elif pcr_regime in ("greed", "fear"):
        pcr_pts = 7
    elif pcr_regime in ("extreme_greed", "extreme_fear"):
        pcr_pts = 3
    else:
        pcr_pts = 5

    # Fear & Greed scoring (10 points)
    if fg_regime == "neutral":
        fg_pts = 10
    elif fg_regime in ("greed", "fear"):
        fg_pts = 7
    elif fg_regime in ("extreme_greed",):
        fg_pts = 4  # Complacency is dangerous
    elif fg_regime in ("extreme_fear",):
        fg_pts = 3  # Panic is very dangerous for condors
    else:
        fg_pts = 5

    pts = pcr_pts + fg_pts
    note = (
        f"PCR={pcr_value:.2f} ({pcr_regime}) | "
        f"Fear&Greed={fg_score:.0f} ({fg_regime})"
        if pcr_value else
        f"PCR=unknown | Fear&Greed={fg_score:.0f} ({fg_regime})"
    )

    return pts, 20, note


def score_flow(flow_data: dict) -> tuple:
    """Flow score — 20 points max."""
    danger = flow_data.get("combined_danger", 0)
    summary = flow_data.get("summary", "")

    if danger == 0:
        pts = 20
        note = "No unusual flow detected"
    elif danger == 1:
        pts = 14
        note = f"Low flow concern: {summary}"
    elif danger == 2:
        pts = 7
        note = f"Medium flow concern: {summary}"
    else:  # danger == 3
        pts = 0
        note = f"HIGH FLOW DANGER: {summary}"

    return pts, 20, note


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

async def build_context(symbol: str = "SPY", chain: dict = None) -> dict:
    """
    Build complete pre-trade context score.

    symbol: primary symbol being evaluated (for flow detection)
    chain: options chain dict from market_data.get_options_chain() (optional)

    Returns full context dict with score, breakdown, and agent recommendations.
    """
    cache_key = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M')[:13]}"  # Cache per hour
    cached = _read_cache(cache_key)
    if cached:
        log.debug(f"Context from cache: {symbol} score={cached.get('score')}")
        return cached

    log.info(f"Building context for {symbol}...")

    # Import data services
    from market_data import get_vix
    from macro_data import get_macro_context
    from sentiment_data import get_sentiment_context
    from flow_detector import run_flow_scan

    # Fetch all data concurrently where possible
    try:
        macro_task = asyncio.create_task(get_macro_context())
        sentiment_task = asyncio.create_task(get_sentiment_context())

        # VIX and flow are synchronous — run in executor
        loop = asyncio.get_event_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            vix_future = loop.run_in_executor(pool, get_vix)
            flow_future = loop.run_in_executor(pool, run_flow_scan, symbol, chain)
            vix_data, flow_data = await asyncio.gather(vix_future, flow_future)

        macro_context = await macro_task
        sentiment_context = await sentiment_task

    except Exception as e:
        log.error(f"Context build failed: {e}")
        # Return safe fallback — SKIP on data failure
        return {
            "score": 35,
            "decision": "caution",
            "error": str(e),
            "summary": f"Context build failed: {e}. Defaulting to CAUTION.",
            "agent1_action": "skip",
            "agent2_action": "skip",
            "fetched_at": datetime.now().isoformat(),
        }

    # Merge VIX term structure into vix_data
    from sentiment_data import get_vix_term_structure
    import concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        vix_term = await loop.run_in_executor(pool, get_vix_term_structure)
    vix_data["term_shape"] = vix_term.get("term_shape", "unknown")
    vix_data["term_stress"] = vix_term.get("term_stress", 0)

    # Score each component
    vix_pts, vix_max, vix_note = score_vix(vix_data)
    event_pts, event_max, event_note = score_event_calendar(macro_context)
    macro_pts, macro_max, macro_note = score_macro(macro_context)
    sentiment_pts, sentiment_max, sentiment_note = score_sentiment(sentiment_context)
    flow_pts, flow_max, flow_note = score_flow(flow_data)

    total_score = vix_pts + event_pts + macro_pts + sentiment_pts + flow_pts
    max_score = vix_max + event_max + macro_max + sentiment_max + flow_max

    # ── Cross-signal sanity checks ──────────────────────────────────────
    cross_signal_notes = []

    # Fix: Macro "healthy" shouldn't contribute full 20 when sentiment is extreme_fear
    fg_regime = sentiment_context.get("fear_greed", {}).get("regime", "unknown")
    if macro_pts >= 18 and fg_regime == "extreme_fear":
        old_macro = macro_pts
        macro_pts = min(macro_pts, 10)  # Cap macro at 10 when sentiment is panicking
        total_score = total_score - old_macro + macro_pts
        cross_signal_notes.append(
            f"Macro capped {old_macro}→{macro_pts}: extreme fear contradicts 'healthy' macro"
        )

    # Fix: Composite danger — VIX backwardation + extreme fear = CAUTION floor
    vix_term = vix_data.get("term_shape", "unknown")
    fg_score_val = sentiment_context.get("fear_greed", {}).get("score", 50)
    vix_val = vix_data.get("vix", 0)

    composite_danger = False
    if vix_term == "backwardation" and fg_score_val < 20:
        composite_danger = True
        cross_signal_notes.append(
            f"COMPOSITE DANGER: VIX backwardation + Fear&Greed={fg_score_val:.0f} (<20)"
        )
    elif vix_val >= 28 and vix_term == "backwardation":
        composite_danger = True
        cross_signal_notes.append(
            f"COMPOSITE DANGER: VIX {vix_val:.1f}≥28 + backwardation"
        )

    normalized_score = round(total_score / max_score * 100)

    # Apply composite danger floor — cap at CAUTION regardless of total score
    if composite_danger and normalized_score > 55:
        normalized_score = 55
        cross_signal_notes.append(f"Score capped at 55 (CAUTION floor) due to composite danger")

    # Decision
    if normalized_score >= 60:
        decision = "proceed"
        decision_label = "PROCEED"
        decision_color = 0x2ECC71  # Green
        agent1_action = "standard"
        agent2_action = "standard"
    elif normalized_score >= 40:
        decision = "caution"
        decision_label = "CAUTION"
        decision_color = 0xF39C12  # Orange
        agent1_action = "widen_wings"   # +$2 to wing width
        agent2_action = "raise_delta"   # Min delta 0.15 instead of 0.10
    else:
        decision = "skip"
        decision_label = "SKIP"
        decision_color = 0xE74C3C  # Red
        agent1_action = "skip"
        agent2_action = "skip"

    # Hard overrides — certain conditions always skip regardless of score
    hard_skip = False
    hard_skip_reason = None

    if macro_context.get("is_event_today"):
        hard_skip = True
        hard_skip_reason = f"EVENT DAY: {macro_context.get('next_event', {}).get('name', 'major event')}"
    elif vix_data.get("vix", 20) >= 35:
        hard_skip = True
        hard_skip_reason = f"VIX {vix_data['vix']:.1f} ≥ 35 — guard halt active"
    elif vix_data.get("regime") == "halt":
        hard_skip = True
        hard_skip_reason = "VIX halt regime active"

    if hard_skip:
        decision = "skip"
        decision_label = "SKIP (HARD)"
        decision_color = 0xE74C3C
        agent1_action = "skip"
        agent2_action = "skip"

    # Agent-specific parameter adjustments
    agent1_params = _get_agent1_params(agent1_action, vix_data, flow_data)
    agent2_params = _get_agent2_params(agent2_action, flow_data)

    context = {
        "score": normalized_score,
        "raw_score": total_score,
        "max_score": max_score,
        "decision": decision,
        "decision_label": decision_label,
        "decision_color": decision_color,
        "hard_skip": hard_skip,
        "hard_skip_reason": hard_skip_reason,

        # Component scores
        "components": {
            "vix":       {"score": vix_pts,       "max": vix_max,       "note": vix_note},
            "event":     {"score": event_pts,      "max": event_max,     "note": event_note},
            "macro":     {"score": macro_pts,      "max": macro_max,     "note": macro_note},
            "sentiment": {"score": sentiment_pts,  "max": sentiment_max, "note": sentiment_note},
            "flow":      {"score": flow_pts,       "max": flow_max,      "note": flow_note},
        },
        "cross_signal_notes": cross_signal_notes,
        "composite_danger": composite_danger,

        # Agent recommendations
        "agent1_action": agent1_action,
        "agent1_params": agent1_params,
        "agent2_action": agent2_action,
        "agent2_params": agent2_params,

        # Raw data for morning brief
        "vix_data": vix_data,
        "macro_context": macro_context,
        "sentiment_context": sentiment_context,
        "flow_data": flow_data,

        # Summary for Discord
        "summary": _build_summary(
            normalized_score, decision_label, vix_note,
            event_note, macro_note, sentiment_note, flow_note, hard_skip_reason
        ),

        "symbol": symbol,
        "fetched_at": datetime.now().isoformat(),
    }

    _write_cache(cache_key, context)
    log.info(
        f"Context {symbol}: score={normalized_score} decision={decision_label} "
        f"[VIX:{vix_pts}/{vix_max} EVENT:{event_pts}/{event_max} "
        f"MACRO:{macro_pts}/{macro_max} SENT:{sentiment_pts}/{sentiment_max} "
        f"FLOW:{flow_pts}/{flow_max}]"
    )
    return context


def _get_agent1_params(action: str, vix_data: dict, flow_data: dict) -> dict:
    """Compute adjusted Agent 1 parameters based on context."""
    base_wing_width = 5.0
    base_short_delta = 0.10

    if action == "skip":
        return {"action": "skip", "reason": "context score too low"}

    wing_width = base_wing_width
    short_delta = base_short_delta

    vix = vix_data.get("vix", 20)

    if action == "widen_wings":
        wing_width = 7.0  # Wider protection
        short_delta = 0.08  # More conservative strikes

    # VIX-based fine-tuning
    if vix > 25:
        wing_width = max(wing_width, 7.0)
        short_delta = min(short_delta, 0.08)
    elif vix > 20:
        wing_width = max(wing_width, 6.0)

    # Flow-based adjustment
    flow_danger = flow_data.get("combined_danger", 0)
    if flow_danger >= 2:
        wing_width = max(wing_width, 7.0)

    return {
        "action": action,
        "wing_width": wing_width,
        "short_delta": short_delta,
        "note": f"Adjusted for context (base: ${base_wing_width} wide @ delta {base_short_delta})",
    }


def _get_agent2_params(action: str, flow_data: dict) -> dict:
    """Compute adjusted Agent 2 parameters based on context."""
    if action == "skip":
        return {"action": "skip", "reason": "context score too low"}

    min_delta = 0.10
    if action == "raise_delta":
        min_delta = 0.15  # More conservative: further OTM

    # Per-symbol flow overrides
    flow_results = flow_data.get("options_flow", {}) or {}
    skip_symbols = []
    if flow_data.get("combined_danger", 0) >= 2:
        skip_symbols = [flow_data.get("symbol")]

    return {
        "action": action,
        "min_delta": min_delta,
        "skip_symbols": [s for s in skip_symbols if s],
        "note": f"Adjusted for context (base: min delta {0.10})",
    }


def _build_summary(score, label, vix_note, event_note, macro_note, sentiment_note, flow_note, hard_skip) -> str:
    lines = [f"**Context Score: {score}/100 — {label}**"]
    if hard_skip:
        lines.append(f"⛔ Hard skip: {hard_skip}")
    lines.extend([
        f"• VIX: {vix_note}",
        f"• Events: {event_note}",
        f"• Macro: {macro_note}",
        f"• Sentiment: {sentiment_note}",
        f"• Flow: {flow_note}",
    ])
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD EMBED BUILDER — For morning brief and trade cards
# ─────────────────────────────────────────────────────────────────────────────

def build_context_embed(context: dict) -> dict:
    """Build a Discord embed from context. Used in morning brief and trade cards."""
    score = context.get("score", 0)
    label = context.get("decision_label", "?")
    color = context.get("decision_color", 0x95A5A6)
    components = context.get("components", {})

    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)

    fields = [
        {
            "name": "Score",
            "value": f"`{score_bar}` **{score}/100** — {label}",
            "inline": False,
        }
    ]

    for name, data in components.items():
        pts = data.get("score", 0)
        max_pts = data.get("max", 0)
        note = data.get("note", "")
        emoji = "✅" if pts >= max_pts * 0.7 else ("⚠️" if pts >= max_pts * 0.4 else "❌")
        fields.append({
            "name": f"{emoji} {name.title()} ({pts}/{max_pts})",
            "value": note[:100],
            "inline": False,
        })

    if context.get("hard_skip_reason"):
        fields.append({
            "name": "⛔ Hard Skip Reason",
            "value": context["hard_skip_reason"],
            "inline": False,
        })

    return {
        "title": f"🧠 Pre-Trade Context: {context.get('symbol', 'Market')}",
        "description": f"Built at {context.get('fetched_at', '')[:16].replace('T', ' ')} ET",
        "color": color,
        "fields": fields,
        "footer": {"text": "QuantAI Context Engine · context_builder.py"},
    }


# CLI test
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    async def main():
        print("\n=== Building SPY Context ===")
        ctx = await build_context("SPY")
        print(f"\nScore: {ctx['score']}/100 — {ctx['decision_label']}")
        print(f"Summary:\n{ctx['summary']}")
        print(f"\nAgent 1 params: {json.dumps(ctx['agent1_params'], indent=2)}")
        print(f"Agent 2 params: {json.dumps(ctx['agent2_params'], indent=2)}")

    asyncio.run(main())
