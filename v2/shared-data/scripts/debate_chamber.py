#!/usr/bin/env python3
"""
QuantAI Debate Chamber
Reads market_intelligence.json and runs a 3-agent Bull/Bear/Judge debate.
Outputs top 2 trade proposals to debate_output.json.

Called by the Orchestrator agent when it wants trade proposals.
Usage: python3 debate_chamber.py [pre_market|mid_session]
"""
import json, os, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
from _llm_client import Client
from _llm_call import call_llm_json
from _debate_cases import build_case

# Auto-load .env from repo root
import pathlib
_env_file = pathlib.Path(__file__).parent.parent.parent.parent / ".env"
if not _env_file.exists():
    _env_file = pathlib.Path(__file__).parent.parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            import os as _os
            if not _os.environ.get(_k.strip()):
                _os.environ[_k.strip()] = _v.strip()


ET = ZoneInfo("America/New_York")
HOME = os.environ.get("QUANTAI_HOME", "/root/quantai-v2")
CACHE = "/root/quantai-v2/shared-data/cache"
LOGS  = "/root/quantai-v2/shared-data/logs"
os.makedirs(CACHE, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

now_et = datetime.now(ET)
hour = now_et.hour
session = "pre_market" if hour < 9 else "morning" if hour < 12 else "afternoon" if hour < 15 else "end_of_day"
client = Client()

SONNET = "claude-sonnet-4-6"
HAIKU  = "claude-haiku-4-5-20251001"

CONSTITUTION = """
IRON-CLAD RULES (never violate):
- max_loss_pct per trade ≤ 2% of account ($1,000 max loss per trade on $50k notional cap)
- Short delta: 0.05 – 0.20 only
- Min credit/income: $0.30 for spreads, $1.00 net debit max for diagonals
- Hard close: 3:30 PM ET latest
- VIX upper bound: 30 for iron condors
- Earnings blackout: 14+ days minimum
- Max daily loss: $1,000 total → halt all
- No trading 9:30–9:45 AM or 3:45–4:00 PM

INCOME GOAL: $1,000/month from agent trades on $20,000 paper account (5%/month).
- That means ~$50/day average across ~20 trading days
- Prefer trades that collect $100-400 premium per position
- Quality over quantity — 1-2 high conviction trades per day beats 5 mediocre ones
- Diagonal spreads (poor man's covered call/put) are PREFERRED for consistent income:
  they collect time decay with defined risk and no shares needed
- Boring and consistent beats exciting and volatile
"""

# ── Load intelligence packet ──────────────────────────────────────────
intel_path = f"{CACHE}/market_intelligence.json"
if not os.path.exists(intel_path):
    print("[debate] ERROR: market_intelligence.json not found — run market_intelligence.py first")
    sys.exit(1)

with open(intel_path) as f:
    packet = json.load(f)

macro   = packet.get("macro", {})
symbols = packet.get("symbols", {})
setups  = packet.get("high_conviction_setups", [])
flags   = packet.get("risk_flags", [])
regime  = packet.get("market_regime", "normal")

print(f"[debate] Loaded packet — regime={regime} setups={len(setups)} flags={len(flags)}")

# Hard stop
if regime == "halt":
    output = {
        "session": session,
        "timestamp": datetime.now(ET).isoformat(),
        "status": "halted",
        "reason": f"Market regime HALT — VIX {macro.get('vix', 0):.1f} ≥ 35",
        "approved_trades": []
    }
    with open(f"{CACHE}/debate_output.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"[debate] HALTED — {output['reason']}")
    sys.exit(0)

# ── Build compact market context ──────────────────────────────────────
spy = symbols.get("SPY", {})
qqq = symbols.get("QQQ", {})

context_lines = [
    f"MARKET INTELLIGENCE — {session.upper()} — {packet.get('timestamp','')}",
    f"Regime: {regime.upper()} | VIX: {macro.get('vix',0):.1f} ({macro.get('vix_regime','?')}) | Term: {macro.get('vix_term_structure','?')}",
    f"Fear & Greed: {macro.get('fear_greed_score',50)} ({macro.get('fear_greed_label','?')})",
    f"Yield curve: {macro.get('yield_curve','?')} | FOMC: {macro.get('fomc_days_away',999)}d | CPI: {macro.get('cpi_days_away',999)}d",
    f"Event today: {macro.get('is_event_day',False)} — {macro.get('event_today','')}",
    "",
    f"SPY: ${spy.get('price',0):.2f} ({spy.get('change_pct',0):+.1f}%) | RSI {spy.get('rsi_14',50):.0f} | MACD {spy.get('macd_signal','?')} | BB {spy.get('bb_position','?')} | Above EMA200: {spy.get('above_ema200',False)}",
    f"QQQ: ${qqq.get('price',0):.2f} ({qqq.get('change_pct',0):+.1f}%) | RSI {qqq.get('rsi_14',50):.0f}",
    "",
    "RISK FLAGS:",
]
for f_ in flags:
    context_lines.append(f"  [{f_['level']}] {f_['reason']}")

context_lines += ["", "HIGH CONVICTION SETUPS (pre-screened):"]
for s in setups:
    context_lines.append(f"  {s['symbol']} {s['setup_type']} — score {s['conviction_score']} — {', '.join(s['reasons'][:2])}")

# Load scan results — credit spreads
spread_path = f"{CACHE}/credit_spread_scan.json"
if os.path.exists(spread_path):
    try:
        spread_data = json.load(open(spread_path))
        tops = spread_data.get("top_opportunities", [])[:5]
        if tops:
            context_lines += ["", "TOP CREDIT SPREAD CANDIDATES (from scanner):"]
            for o in tops:
                context_lines.append(
                    f"  {o['symbol']} ${o['price']:.0f} {o['strategy']} "
                    f"short@{o['short_strike']} long@{o['long_strike']} "
                    f"exp:{o['expiry']} credit:${o['credit']:.2f} "
                    f"IVR:{o.get('iv_rank','?')} RSI:{o.get('rsi','?'):.0f}"
                )
    except:
        pass

# Load diagonal scan results
diag_path = f"{CACHE}/diagonal_scan.json"
if os.path.exists(diag_path):
    try:
        diag_data = json.load(open(diag_path))
        tops = diag_data.get("top_opportunities", [])[:5]
        if tops:
            context_lines += ["", "TOP DIAGONAL SPREAD CANDIDATES (poor man's covered call/put):"]
            for o in tops:
                context_lines.append(
                    f"  {o['symbol']} ${o['price']:.0f} {o['option_type'].upper()} diagonal "
                    f"strike@{o['strike']} near:{o['near_expiry']} far:{o['far_expiry']} "
                    f"net_debit:${o['net_debit']:.2f} est_profit:${o['est_max_profit']:.2f} "
                    f"IVR:{o.get('iv_rank','?')} RSI:{o.get('rsi','?'):.0f}"
                )
    except:
        pass

# Load condor scan results
condor_path = f"{CACHE}/condor_scan.json"
if os.path.exists(condor_path):
    try:
        condor_data = json.load(open(condor_path))
        tops = condor_data.get("top_opportunities", [])[:3]
        if tops:
            context_lines += ["", "TOP IRON CONDOR CANDIDATES:"]
            for o in tops:
                context_lines.append(
                    f"  {o['symbol']} ${o['price']:.0f} condor "
                    f"puts:{o['put_long']}/{o['put_short']} calls:{o['call_short']}/{o['call_long']} "
                    f"exp:{o['expiry']} credit:${o['credit']:.2f} IVR:{o.get('iv_rank','?')}"
                )
    except:
        pass

context_lines += ["", "SYMBOL DATA:"]
for sym in list(symbols.keys())[:12]:  # Show all symbols, not just 7
    s = symbols.get(sym, {})
    if s:
        context_lines.append(
            f"  {sym}: ${s.get('price',0):.2f} RSI:{s.get('rsi_14',50):.0f} "
            f"MACD:{s.get('macd_signal','?')} Earn:{s.get('next_earnings_days',999)}d "
            f"EMA200:{'above' if s.get('above_ema200') else 'below'}"
        )

market_context = "\n".join(context_lines)

# ─────────────────────────────────────────────────────────────────────
# STEP 1: PROPOSAL AGENT
# ─────────────────────────────────────────────────────────────────────
print("[debate] Step 1: Proposal Agent generating candidates...")

_proposal_system = f"""You are QuantAI's Trade Proposal Agent. Generate 3-5 specific options trade candidates.

{CONSTITUTION}

Strategies allowed for autonomous execution (all are defined-risk, no share ownership needed):
- diagonal_spread: PREFERRED STRATEGY. Sell near-term option, buy further-dated same strike.
  Poor man's covered call (call diagonal) or poor man's covered put (put diagonal).
  Net debit to enter (~$200-500). Max loss = net debit. Max profit = 2-3x net debit.
  Best when: IV rank > 40, stock near target strike, 2-3 month time horizon.
  Use scanner diagonal candidates — they have pre-verified liquidity and two expiries.
- bull_put_spread: sell put + buy lower put. Bullish/neutral, stock above key support.
- bear_call_spread: sell call + buy higher call. Bearish/neutral, stock below resistance.
- iron_condor: bull put + bear call combined. Range-bound market, VIX 13-28.
- iron_butterfly: sell ATM straddle + buy wings. Very tight range, high IV crush expected.
- jade_lizard: sell put spread + sell OTM call. Strong bullish conviction + high IV.
- calendar_spread: sell near-term, buy far-term same strike. Low IV environment only.

INCOME GOAL: Propose trades that collectively target $50/day ($1,000/month).
- Prefer diagonal spreads: consistent time decay income, defined risk, no shares
- Each trade should collect or position for $100-400 in premium/profit
- 1-2 high conviction trades per day. Never force a trade just to trade.

Ticker selection:
- Use the pre-screened scanner candidates above — they already pass liquidity gates
- Any ticker with avg volume >5M, options OI >200, bid/ask <$0.15, no earnings within 14 days
- For diagonals: use exactly the near_expiry and far_expiry from scanner output

DO NOT propose: covered_call, collar, cash_secured_put — require owning shares.
Every proposal MUST have fully defined max loss. No naked short options ever.
Every proposal MUST include: symbol, strategy, specific strikes, expiration, estimated_credit, max_loss_pct, probability_of_profit, thesis (1 sentence), invalidation (1 sentence).
If regime is risk_off or halt: output 0 proposals.
Output ONLY valid JSON, no markdown:
{{"proposals":[{{"id":"P1","symbol":"SPY","strategy":"iron_condor","legs":[{{"action":"sell","type":"put","strike":555,"expiry":"0DTE"}},{{"action":"buy","type":"put","strike":550,"expiry":"0DTE"}},{{"action":"sell","type":"call","strike":575,"expiry":"0DTE"}},{{"action":"buy","type":"call","strike":580,"expiry":"0DTE"}}],"estimated_credit":0.75,"max_loss":4.25,"max_loss_pct":1.7,"probability_of_profit":68,"thesis":"SPY range-bound, VIX 17 contango.","invalidation":"SPY breaks above 572 or below 558."}}],"market_summary":"2 sentences."}}"""

proposal_data = call_llm_json(
    model=SONNET, system=_proposal_system, user=market_context,
    max_tokens=2000, caller="debate_proposal",
)
if not proposal_data:
    proposal_data = {"proposals": [], "market_summary": "LLM call failed after retries"}

def _to_float(v, default=0.0):
    """Coerce LLM-returned values to float. Handles None, numeric strings,
    strings with $/%/commas, and out-of-vocab text."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "").replace("%", "")
        try:
            return float(s)
        except ValueError:
            return default
    return default


def _normalize_proposal(p: dict) -> dict:
    """Coerce all numeric fields the formatter touches to floats with safe
    defaults. The LLM occasionally returns strings ('1.7') or null instead of
    numbers; without normalization the f-string formatter raises TypeError /
    ValueError and the whole debate cycle aborts."""
    p["estimated_credit"] = _to_float(p.get("estimated_credit"), 0.0)
    p["max_loss"] = _to_float(p.get("max_loss"), 0.0)
    p["max_loss_pct"] = _to_float(p.get("max_loss_pct"), 0.0)
    p["probability_of_profit"] = _to_float(p.get("probability_of_profit"), 0.0)
    return p


proposals = [_normalize_proposal(p) for p in proposal_data.get("proposals", []) if isinstance(p, dict)]
market_summary = proposal_data.get("market_summary", "")
print(f"[debate] Proposal Agent: {len(proposals)} candidates")

if not proposals:
    output = {
        "session": session, "timestamp": datetime.now(ET).isoformat(),
        "status": "no_proposals", "market_summary": market_summary,
        "approved_trades": []
    }
    with open(f"{CACHE}/debate_output.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"[debate] No proposals generated — {market_summary}")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────
# STEP 2: BULL & BEAR (sequential to avoid rate limits)
# ─────────────────────────────────────────────────────────────────────
print("[debate] Step 2: Bull & Bear agents...")

bull_bear_results = []
for prop in proposals:
    trade_desc = (
        f"Trade: {prop.get('strategy','?').upper()} on {prop.get('symbol','?')}\n"
        f"Legs: {json.dumps(prop.get('legs',[]))}\n"
        f"Credit: ${prop.get('estimated_credit',0):.2f} | Max Loss: {prop.get('max_loss_pct',0):.1f}%\n"
        f"Prob of Profit: {prop.get('probability_of_profit',0)}%\n"
        f"Thesis: {prop.get('thesis','')}\n"
        f"Market: VIX {macro.get('vix',0):.1f} ({macro.get('vix_regime','?')})"
    )

    bull_case = build_case("bull", prop, macro, regime, flags)
    bear_case = build_case("bear", prop, macro, regime, flags)
    bull_bear_results.append((bull_case, bear_case))
    print(f"[debate]   {prop.get('id','?')} {prop.get('symbol','?')} — Bull/Bear done")

# ─────────────────────────────────────────────────────────────────────
# STEP 3: JUDGE
# ─────────────────────────────────────────────────────────────────────
print("[debate] Step 3: Judge Agent selecting top 2...")

debate_text = ""
for prop, (bull, bear) in zip(proposals, bull_bear_results):
    debate_text += f"""
═══ {prop.get('id','?')}: {prop.get('strategy','?').upper()} on {prop.get('symbol','?')} ═══
Credit: ${prop.get('estimated_credit',0):.2f} | Max Loss: {prop.get('max_loss_pct',0):.1f}% | POP: {prop.get('probability_of_profit',0)}%
Thesis: {prop.get('thesis','')}
BULL: {bull}
BEAR: {bear}
"""

_judge_system = f"""You are the Judge Agent. Score each trade and select exactly 2 to execute.
{CONSTITUTION}
Score 0-100 per trade: risk/reward quality (25), macro timing (25), bear argument strength (25, penalize if devastating), guard compliance (25).
Select exactly 2 (or fewer if < 2 survive). Output ONLY valid JSON:
{{"scored_trades":[{{"id":"P1","net_score":74,"verdict":"APPROVED","reasoning":"1 sentence."}}],"approved_ids":["P1","P3"],"judge_summary":"2 sentences on why these 2."}}"""

judge_data = call_llm_json(
    model=SONNET, system=_judge_system,
    user=f"VIX {macro.get('vix',0):.1f} | Regime: {regime}\n\n{debate_text}\n\nSelect the 2 best trades.",
    max_tokens=1200, caller="debate_judge",
)
if not judge_data:
    judge_data = {"scored_trades": [], "approved_ids": [], "judge_summary": "LLM call failed after retries"}

approved_ids   = judge_data.get("approved_ids", [])
judge_summary  = judge_data.get("judge_summary", "")
score_map      = {s["id"]: s for s in judge_data.get("scored_trades", [])}
print(f"[debate] Judge approved: {approved_ids} — {judge_summary}")

# ─────────────────────────────────────────────────────────────────────
# STEP 4: BUILD OUTPUT
# ─────────────────────────────────────────────────────────────────────
approved_trades = []
for prop, (bull, bear) in zip(proposals, bull_bear_results):
    pid = prop.get("id")
    if pid not in approved_ids:
        continue
    score = score_map.get(pid, {})
    approved_trades.append({
        "proposal": {**prop, "source": "agent"},  # mark as agent-proposed for sheets
        "bull_case": bull,
        "bear_case": bear,
        "judge_score": score.get("net_score", 0),
        "judge_reasoning": score.get("reasoning", ""),
    })

output = {
    "session": session,
    "timestamp": datetime.now(ET).isoformat(),
    "status": "complete",
    "market_summary": market_summary,
    "judge_summary": judge_summary,
    "proposals_generated": len(proposals),
    "approved_count": len(approved_trades),
    "approved_trades": approved_trades,
    "all_scores": judge_data.get("scored_trades", []),
}

with open(f"{CACHE}/debate_output.json", "w") as f:
    json.dump(output, f, indent=2, default=str)

# Append to debate log
log_entry = {
    "timestamp": datetime.now(ET).isoformat(),
    "session": session,
    "proposals_generated": len(proposals),
    "approved_count": len(approved_trades),
    "approved_ids": approved_ids,
    "judge_summary": judge_summary,
}
with open(f"{LOGS}/debate_log.jsonl", "a") as f:
    f.write(json.dumps(log_entry) + "\n")

print(f"\n[debate] ✅ Done — {len(proposals)} proposed → {len(approved_trades)} approved")
print(f"[debate] Saved → {CACHE}/debate_output.json")

# ── Print trade cards for agent to forward to Discord ─────────────────
print("\n" + "="*60)
print("TRADE CARDS (post these to #trade-proposals):")
print("="*60)
for item in approved_trades:
    prop = item["proposal"]
    legs_str = "\n".join(
        f"  {l.get('action','').upper()} {l.get('type','').upper()} ${l.get('strike','?')} {l.get('expiry','?')}"
        for l in prop.get("legs", [])
    )
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 DEBATE PROPOSAL — {prop.get('symbol','?')} {prop.get('strategy','?').replace('_',' ').upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Legs:
{legs_str}
Credit: ${prop.get('estimated_credit',0):.2f} | Max Loss: {prop.get('max_loss_pct',0):.1f}% | POP: {prop.get('probability_of_profit',0)}%
Debate Score: {item['judge_score']}/100

Thesis: {prop.get('thesis','')}
Invalidation: {prop.get('invalidation','')}

🐂 Bull: {item['bull_case'][:300]}

🐻 Bear: {item['bear_case'][:300]}

⚖️ Judge: {item['judge_reasoning']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
React ✅ approve · ❌ reject · 🔄 defer
""")
