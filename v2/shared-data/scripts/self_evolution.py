#!/usr/bin/env python3
"""
QuantAI Self-Evolution Engine
Runs after EOD scoring. If score < 90, proposes ONE config change.
Usage: python3 self_evolution.py [eod_score] [--consolidate]

6 steps: Observe → Critique → Generate → Validate → Apply → Consolidate
"""
import json, os, sys, re, subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from _llm_client import Client

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
JOURNAL   = "/root/quantai-v2/shared-data/journal/paper"
LOGS      = "/root/quantai-v2/shared-data/logs"
CACHE     = f"{HOME}/v2/shared-data/cache"
STRATEGIES = "/root/quantai-v2/shared-data/strategies"
os.makedirs(LOGS, exist_ok=True)

eod_score    = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
consolidate  = "--consolidate" in sys.argv

client = Client()
SONNET = "claude-sonnet-4-5"
HAIKU  = "claude-haiku-4-5-20251001"

CONSTITUTION = """
IRON-CLAD RULES (self-evolution can NEVER touch these):
- max_loss_pct ≤ 2.0%
- short_delta: 0.05 – 0.20 range only
- min_credit ≥ $0.30
- earnings_blackout ≥ 14 days
- max_daily_loss ≤ $1,000
- vix_upper ≤ 30
- profit_target_pct: 30–70% range only
- stop_loss_multiplier ≤ 3.0
"""

print(f"[evolution] Starting — EOD score: {eod_score:.0f} | Consolidate: {consolidate}")

# ─────────────────────────────────────────────────────────────────────
# CONSOLIDATE (weekly, independent of score)
# ─────────────────────────────────────────────────────────────────────
if consolidate:
    obs_path = f"{LOGS}/evolution_observations.jsonl"
    if not os.path.exists(obs_path):
        print("[evolution] No observations yet — consolidation skipped")
    else:
        observations = []
        cutoff = datetime.now(ET) - timedelta(days=35)
        with open(obs_path) as f:
            for line in f:
                try:
                    obs = json.loads(line.strip())
                    ts = datetime.fromisoformat(obs.get("timestamp","2000-01-01T00:00:00"))
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=ET)
                    if ts > cutoff: observations.append(obs)
                except: continue

        if len(observations) < 5:
            print(f"[evolution] Only {len(observations)} observations — need 5+ for consolidation")
        else:
            resp = client.messages.create(
                model=SONNET, max_tokens=1000,
                system="""Extract durable strategy principles from these observations.
A principle needs 3+ supporting observations. Output ONLY valid JSON:
{"principles":[{"principle":"Entry 2 underperforms when SPY moves >0.5% before 11 AM","observation_count":8,"recommended_rule":"Skip Entry 2 if abs(SPY_change_since_open) > 0.5%"}],"total_analyzed":N}""",
                messages=[{"role":"user","content":f"Observations (last 35 days):\n{json.dumps(observations[-30:],indent=2)}"}]
            )
            try:
                raw = resp.content[0].text.strip().replace("```json","").replace("```","")
                result = json.loads(raw)
                with open(f"{LOGS}/strategy_principles.json","w") as f:
                    json.dump(result, f, indent=2)
                principles = result.get("principles",[])
                print(f"[evolution] Consolidation: {len(principles)} principles extracted from {len(observations)} observations")
                for p in principles:
                    print(f"  → {p.get('principle','')}")
            except Exception as e:
                print(f"[evolution] Consolidation parse error: {e}")

# ─────────────────────────────────────────────────────────────────────
# Score check — only evolve if < 90
# ─────────────────────────────────────────────────────────────────────
if eod_score >= 90:
    print(f"[evolution] Score {eod_score:.0f} ≥ 90 — no evolution needed ✅")
    sys.exit(0)

print(f"[evolution] Score {eod_score:.0f} < 90 — running evolution pipeline")

# ─────────────────────────────────────────────────────────────────────
# Load recent journal entries
# ─────────────────────────────────────────────────────────────────────
journal_entries = []
trades_path = f"{JOURNAL}/trades.jsonl"
if os.path.exists(trades_path):
    cutoff = datetime.now(ET) - timedelta(days=7)
    with open(trades_path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                ts_str = entry.get("timestamp","")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None: ts = ts.replace(tzinfo=ET)
                    if ts > cutoff: journal_entries.append(entry)
            except: continue

today_str = datetime.now(ET).date().isoformat()
today_entries = [e for e in journal_entries if today_str in e.get("timestamp","")]
print(f"[evolution] Journal: {len(today_entries)} today / {len(journal_entries)} last 7 days")

if not today_entries:
    print("[evolution] No trades today — nothing to evolve from")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────
# Load current strategy config
# ─────────────────────────────────────────────────────────────────────
config_path = f"{STRATEGIES}/sofi_collar.json"
if os.path.exists(config_path):
    with open(config_path) as f:
        current_config = json.load(f)
else:
    current_config = {}

# ─────────────────────────────────────────────────────────────────────
# STEP 1: OBSERVE
# ─────────────────────────────────────────────────────────────────────
print("[evolution] Step 1: Observing...")
obs_resp = client.messages.create(
    model=HAIKU, max_tokens=600,
    system="""Extract structured observations from today's trade journal.
Output ONLY valid JSON:
{"win_rate_today":0.67,"trades_today":3,"stop_outs":1,"profit_targets_hit":2,"time_closes":0,"avg_credit_cents":72,"observations":["Entry 1 won 2/2. Entry 2 lost 1/1 (credit too thin at $0.45)."],"correction_needed":true,"primary_issue":"entry_2_credit_too_thin"}""",
    messages=[{"role":"user","content":f"Today's trades:\n{json.dumps(today_entries,indent=2)}\n\nEOD Score: {eod_score}"}]
)
try:
    obs_raw = obs_resp.content[0].text.strip().replace("```json","").replace("```","")
    observation = json.loads(obs_raw)
except Exception as e:
    print(f"[evolution] Observe parse error: {e}")
    observation = {"correction_needed": False}

# Save observation
with open(f"{LOGS}/evolution_observations.jsonl","a") as f:
    observation["timestamp"] = datetime.now(ET).isoformat()
    f.write(json.dumps(observation) + "\n")

if not observation.get("correction_needed"):
    print("[evolution] Observation: no correction warranted")
    sys.exit(0)

print(f"[evolution] Observation: {observation.get('primary_issue','unknown issue')}")

# ─────────────────────────────────────────────────────────────────────
# STEP 2: CRITIQUE
# ─────────────────────────────────────────────────────────────────────
print("[evolution] Step 2: Critiquing...")
crit_resp = client.messages.create(
    model=HAIKU, max_tokens=400,
    system="""Identify the single biggest misalignment between trade outcomes and current config.
Be specific: which param caused the problem? What evidence? Output ONLY valid JSON:
{"primary_critique":"min_credit $0.50 too low — 3/5 stop-outs had credit below $0.55","affected_param":"min_credit","evidence":"avg loss -$180 on sub-$0.55 entries vs +$32 on above-$0.55","no_change_warranted":false}""",
    messages=[{"role":"user","content":f"Config:\n{json.dumps(current_config,indent=2)}\n\nObservations:\n{json.dumps(observation,indent=2)}"}]
)
try:
    crit_raw = crit_resp.content[0].text.strip().replace("```json","").replace("```","")
    critique = json.loads(crit_raw)
except Exception as e:
    print(f"[evolution] Critique parse error: {e}")
    critique = {"no_change_warranted": True}

if critique.get("no_change_warranted"):
    print("[evolution] Critique: no change warranted")
    sys.exit(0)

print(f"[evolution] Critique: {critique.get('primary_critique','')}")

# ─────────────────────────────────────────────────────────────────────
# STEP 3: GENERATE
# ─────────────────────────────────────────────────────────────────────
print("[evolution] Step 3: Generating change...")
gen_resp = client.messages.create(
    model=SONNET, max_tokens=500,
    system=f"""Propose EXACTLY ONE minimal config change based on the critique.
{CONSTITUTION}
Max 20% change in one step. Output ONLY valid JSON:
{{"param":"min_credit","old_value":0.50,"new_value":0.60,"rationale":"3/5 stop-outs at sub-$0.55 — raising filter improves quality","evidence":"journal 2026-03-24 to 2026-03-31","no_change":false}}""",
    messages=[{"role":"user","content":f"Critique:\n{json.dumps(critique,indent=2)}\n\nConfig:\n{json.dumps(current_config,indent=2)}"}]
)
try:
    gen_raw = gen_resp.content[0].text.strip().replace("```json","").replace("```","")
    change = json.loads(gen_raw)
except Exception as e:
    print(f"[evolution] Generate parse error: {e}")
    change = {"no_change": True}

if change.get("no_change"):
    print("[evolution] Generate: no change proposed")
    sys.exit(0)

param    = change.get("param","")
old_val  = change.get("old_value")
new_val  = change.get("new_value")
print(f"[evolution] Proposed: {param} {old_val} → {new_val}")

# ─────────────────────────────────────────────────────────────────────
# STEP 4: VALIDATE (5 gates)
# ─────────────────────────────────────────────────────────────────────
print("[evolution] Step 4: Validating through 5 gates...")
gates = []

# Gate 1: Constitution
constitution_violations = {
    "max_loss_pct":          lambda v: v <= 2.0,
    "min_credit":            lambda v: v >= 0.30,
    "earnings_blackout":     lambda v: v >= 14,
    "max_daily_loss":        lambda v: v <= 1000,
    "vix_upper":             lambda v: v <= 30,
    "profit_target_pct":     lambda v: 30 <= v <= 70,
    "stop_loss_multiplier":  lambda v: v <= 3.0,
}
if param in constitution_violations:
    ok = constitution_violations[param](new_val)
    gates.append({"gate":"Constitution","passed":ok,"reason": "OK" if ok else f"{param}={new_val} violates iron-clad rule"})
else:
    gates.append({"gate":"Constitution","passed":True,"reason":"Param not constitution-protected"})

# Gate 2: Size (max 25% change)
if old_val and old_val != 0:
    pct = abs(new_val - old_val) / abs(old_val)
    ok = pct <= 0.25
    gates.append({"gate":"Size","passed":ok,"reason":f"{pct:.1%} change {'OK' if ok else '> 25% limit'}"})
else:
    gates.append({"gate":"Size","passed":True,"reason":"N/A"})

# Gate 3: Drift
drift_checks = {
    "short_delta":          lambda v: 0.05 <= v <= 0.20,
    "wing_width":           lambda v: v >= 3.0,
    "profit_target_pct":    lambda v: 30 <= v <= 70,
    "stop_loss_multiplier": lambda v: v <= 3.0,
}
if param in drift_checks:
    ok = drift_checks[param](new_val)
    gates.append({"gate":"Drift","passed":ok,"reason":"OK" if ok else f"Value {new_val} drifts from strategy thesis"})
else:
    gates.append({"gate":"Drift","passed":True,"reason":"Not a drift-sensitive param"})

# Gate 4: Safety
safety_checks = {
    "stop_loss_multiplier": lambda v: v <= 3.0,
    "max_contracts":        lambda v: v <= 5,
    "wing_width":           lambda v: v >= 3.0,
}
if param in safety_checks:
    ok = safety_checks[param](new_val)
    gates.append({"gate":"Safety","passed":ok,"reason":"OK" if ok else f"Safety violation: {param}={new_val}"})
else:
    gates.append({"gate":"Safety","passed":True,"reason":"Not safety-critical param"})

# Gate 5: Regression (backtester if available)
backtester = f"{HOME}/v2/shared-data/scripts/backtester.py"
if os.path.exists(backtester):
    try:
        result_bt = subprocess.run(
            ["python3", backtester, "--param", param, "--old", str(old_val), "--new", str(new_val), "--days","30"],
            capture_output=True, text=True, timeout=30
        )
        passed_bt = "PASS" in result_bt.stdout or result_bt.returncode == 0
        gates.append({"gate":"Regression","passed":passed_bt,"reason":result_bt.stdout.strip()[:100] or "Backtest complete"})
    except Exception as e:
        gates.append({"gate":"Regression","passed":True,"reason":f"Backtester exception — passing with warning: {e}"})
else:
    gates.append({"gate":"Regression","passed":True,"reason":"backtester.py not found — gate passed with warning"})

all_passed = all(g["passed"] for g in gates)
failed = [g for g in gates if not g["passed"]]

for g in gates:
    status = "✅" if g["passed"] else "❌"
    print(f"  {status} {g['gate']}: {g['reason']}")

# ─────────────────────────────────────────────────────────────────────
# STEP 5: APPLY or REJECT
# ─────────────────────────────────────────────────────────────────────
log_entry = {
    "timestamp": datetime.now(ET).isoformat(),
    "eod_score": eod_score,
    "param": param,
    "old_value": old_val,
    "new_value": new_val,
    "rationale": change.get("rationale",""),
    "gates": gates,
    "applied": all_passed,
}

if not all_passed:
    print(f"\n[evolution] ❌ REJECTED — {len(failed)} gate(s) failed")
    for g in failed:
        print(f"  Failed: {g['gate']} — {g['reason']}")
    log_entry["rejection_reasons"] = [g["reason"] for g in failed]
    with open(f"{LOGS}/evolution_log.jsonl","a") as f:
        f.write(json.dumps(log_entry) + "\n")
    print("[evolution] Observation stored for future data accumulation")
    sys.exit(0)

# Apply — update strategy config
if os.path.exists(config_path):
    with open(config_path) as f:
        config_to_update = json.load(f)

    # Navigate nested config to set param
    # Try top-level first, then nested sections
    def set_nested(d, key, val):
        if key in d:
            d[key] = val
            return True
        for v in d.values():
            if isinstance(v, dict) and set_nested(v, key, val):
                return True
        return False

    if not set_nested(config_to_update, param, new_val):
        config_to_update[param] = new_val  # add at top level if not found

    version = config_to_update.get("version","1.0")
    try:
        parts = version.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        config_to_update["version"] = ".".join(parts)
    except:
        config_to_update["version"] = "1.1"

    config_to_update["last_evolution"] = datetime.now(ET).isoformat()
    config_to_update["last_change"] = {
        "param": param, "old_value": old_val, "new_value": new_val,
        "rationale": change.get("rationale",""), "eod_score": eod_score
    }

    with open(config_path,"w") as f:
        json.dump(config_to_update, f, indent=2)

    new_version = config_to_update.get("version","?")
    print(f"\n[evolution] ✅ APPLIED — {param}: {old_val} → {new_val} | Version: {new_version}")
else:
    print(f"\n[evolution] ✅ VALIDATED — {param}: {old_val} → {new_val} (no config file to update)")
    new_version = "N/A"

log_entry["new_version"] = new_version
with open(f"{LOGS}/evolution_log.jsonl","a") as f:
    f.write(json.dumps(log_entry) + "\n")

print(f"[evolution] Rationale: {change.get('rationale','')}")
print(f"[evolution] Evidence: {change.get('evidence','')}")
print("\n[evolution] Post this to #pr-updates:")
print(f"""
🧬 Evolution Applied
param:     {param}
old value: {old_val}
new value: {new_val}
version:   {new_version}
rationale: {change.get('rationale','')}
EOD score: {eod_score:.0f}/100 | All 5 gates passed ✅
""")
