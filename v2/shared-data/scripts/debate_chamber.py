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
import anthropic

ET = ZoneInfo("America/New_York")
HOME = os.environ.get("QUANTAI_HOME", "/root/quantai-v2")
CACHE = f"{HOME}/v2/shared-data/cache"
LOGS  = f"{HOME}/v2/shared-data/logs"
os.makedirs(CACHE, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

session = sys.argv[1] if len(sys.argv) > 1 else "pre_market"
client  = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SONNET = "claude-sonnet-4-5"
HAIKU  = "claude-haiku-4-5-20251001"

CONSTITUTION = """
IRON-CLAD RULES (never violate):
- max_loss_pct per trade ≤ 2% of account
- Short delta: 0.05 – 0.20 only
- Min credit: $0.30
- Hard close: 3:30 PM ET latest
- VIX upper bound: 30 for iron condors
- Earnings blackout: 14+ days minimum
- Max daily loss: $1,000 total → halt all
- No trading 9:30–9:45 AM or 3:45–4:00 PM
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

context_lines += ["", "SYMBOL DATA:"]
for sym in ["PLTR","TSM","MU","AMD","AVGO","ASML","SOFI"]:
    s = symbols.get(sym, {})
    if s:
        context_lines.append(f"  {sym}: ${s.get('price',0):.2f} RSI:{s.get('rsi_14',50):.0f} MACD:{s.get('macd_signal','?')} Earn:{s.get('next_earnings_days',999)}d")

market_context = "\n".join(context_lines)

# ─────────────────────────────────────────────────────────────────────
# STEP 1: PROPOSAL AGENT
# ─────────────────────────────────────────────────────────────────────
print("[debate] Step 1: Proposal Agent generating candidates...")

proposal_resp = client.messages.create(
    model=SONNET,
    max_tokens=2000,
    system=f"""You are QuantAI's Trade Proposal Agent. Generate 3-5 specific options trade candidates.

{CONSTITUTION}

Strategies allowed: SPY/QQQ iron condors, covered calls on PLTR/TSM/MU/AMD/AVGO/ASML/SOFI, bull put spreads.
Every proposal MUST include: symbol, strategy, specific strikes, expiration, estimated_credit, max_loss_pct, probability_of_profit, thesis (1 sentence), invalidation (1 sentence).
If regime is risk_off or halt: output 0 proposals.
Output ONLY valid JSON, no markdown:
{{"proposals":[{{"id":"P1","symbol":"SPY","strategy":"iron_condor","legs":[{{"action":"sell","type":"put","strike":555,"expiry":"0DTE"}},{{"action":"buy","type":"put","strike":550,"expiry":"0DTE"}},{{"action":"sell","type":"call","strike":575,"expiry":"0DTE"}},{{"action":"buy","type":"call","strike":580,"expiry":"0DTE"}}],"estimated_credit":0.75,"max_loss":4.25,"max_loss_pct":1.7,"probability_of_profit":68,"thesis":"SPY range-bound, VIX 17 contango.","invalidation":"SPY breaks above 572 or below 558."}}],"market_summary":"2 sentences."}}""",
    messages=[{"role": "user", "content": market_context}]
)

try:
    raw = proposal_resp.content[0].text.strip().replace("```json","").replace("```","").strip()
    proposal_data = json.loads(raw)
except Exception as e:
    print(f"[debate] Proposal parse error: {e}\nRaw: {raw[:300]}")
    proposal_data = {"proposals": [], "market_summary": "Parse error"}

proposals = proposal_data.get("proposals", [])
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

    bull_resp = client.messages.create(
        model=HAIKU, max_tokens=350,
        system="You are the Bull Agent. Make the STRONGEST 3-5 bullet-point case FOR this trade. Use specific numbers. Be direct.",
        messages=[{"role":"user","content":trade_desc}]
    )
    time.sleep(0.5)

    bear_resp = client.messages.create(
        model=HAIKU, max_tokens=350,
        system="You are the Bear Agent. Make the STRONGEST 3-5 bullet-point case AGAINST this trade. Identify every real risk. Be brutal and specific.",
        messages=[{"role":"user","content":trade_desc}]
    )
    time.sleep(0.5)

    bull_bear_results.append((bull_resp.content[0].text.strip(), bear_resp.content[0].text.strip()))
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

judge_resp = client.messages.create(
    model=SONNET, max_tokens=1200,
    system=f"""You are the Judge Agent. Score each trade and select exactly 2 to execute.
{CONSTITUTION}
Score 0-100 per trade: risk/reward quality (25), macro timing (25), bear argument strength (25, penalize if devastating), guard compliance (25).
Select exactly 2 (or fewer if < 2 survive). Output ONLY valid JSON:
{{"scored_trades":[{{"id":"P1","net_score":74,"verdict":"APPROVED","reasoning":"1 sentence."}}],"approved_ids":["P1","P3"],"judge_summary":"2 sentences on why these 2."}}""",
    messages=[{"role":"user","content":f"VIX {macro.get('vix',0):.1f} | Regime: {regime}\n\n{debate_text}\n\nSelect the 2 best trades."}]
)

try:
    raw_j = judge_resp.content[0].text.strip().replace("```json","").replace("```","").strip()
    judge_data = json.loads(raw_j)
except Exception as e:
    print(f"[debate] Judge parse error: {e}")
    judge_data = {"scored_trades":[], "approved_ids":[], "judge_summary":"Parse error"}

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
        "proposal": prop,
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
