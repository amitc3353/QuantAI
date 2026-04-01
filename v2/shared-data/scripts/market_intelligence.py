#!/usr/bin/env python3
"""
QuantAI Market Intelligence
On-demand: run whenever an agent needs fresh market context.
Outputs: /root/quantai-v2/v2/shared-data/cache/market_intelligence.json

Agents call this before any trade proposal or when conditions may have changed.
No fixed schedule — agents decide when they need fresh data.

Usage:
  python3 market_intelligence.py           # auto-detect session from time
  python3 market_intelligence.py --force   # force refresh regardless of age
"""
import json, os, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
CACHE = f"{HOME}/v2/shared-data/cache"
os.makedirs(CACHE, exist_ok=True)

force = "--force" in sys.argv
now_et = datetime.now(ET)
hour = now_et.hour

# Auto-detect session context from time of day
if hour < 9:
    session = "pre_market"
elif hour < 12:
    session = "morning"
elif hour < 15:
    session = "afternoon"
else:
    session = "end_of_day"

# Check if packet is fresh enough (skip if < 90 min old unless forced)
packet_path = f"{CACHE}/market_intelligence.json"
if not force and os.path.exists(packet_path):
    try:
        with open(packet_path) as f:
            existing = json.load(f)
        ts_str = existing.get("timestamp", "")
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        age_minutes = (now_et - ts).total_seconds() / 60
        if age_minutes < 90:
            print(f"[market_intelligence] Packet is {age_minutes:.0f}min old — still fresh. Use --force to override.")
            print(f"[market_intelligence] Regime: {existing.get('market_regime','?')} | VIX: {existing.get('macro',{}).get('vix','?')}")
            sys.exit(0)
    except Exception:
        pass

print(f"[market_intelligence] Building {session} packet — {now_et.strftime('%H:%M ET')}")

# ── yfinance ──────────────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("[market_intelligence] ERROR: yfinance not installed")
    sys.exit(1)

result = {
    "session": session,
    "timestamp": datetime.now(ET).isoformat(),
    "market_regime": "normal",
    "macro": {},
    "symbols": {},
    "risk_flags": [],
    "high_conviction_setups": [],
    "open_positions_summary": "Check Alpaca for live positions",
    "data_quality": 100,
}

# ── VIX ──────────────────────────────────────────────────────────────
try:
    vix_ticker = yf.Ticker("^VIX")
    vix3m_ticker = yf.Ticker("^VIX3M")
    vix_hist = vix_ticker.history(period="3d")
    vix3m_hist = vix3m_ticker.history(period="3d")

    vix = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else 0.0
    vix3m = float(vix3m_hist["Close"].iloc[-1]) if not vix3m_hist.empty else 0.0

    if vix <= 0:      regime = "unknown"
    elif vix < 13:    regime = "low"
    elif vix < 18:    regime = "normal"
    elif vix < 24:    regime = "elevated"
    elif vix < 30:    regime = "high"
    elif vix < 35:    regime = "danger"
    else:             regime = "HALT"

    term_structure = "backwardation" if (vix3m > 0 and vix > vix3m) else "contango"

    result["macro"]["vix"] = round(vix, 2)
    result["macro"]["vix_3m"] = round(vix3m, 2)
    result["macro"]["vix_regime"] = regime
    result["macro"]["vix_term_structure"] = term_structure
    print(f"[market_intelligence] VIX: {vix:.1f} ({regime}) | Term: {term_structure}")

    if regime == "HALT":
        result["risk_flags"].append({"level": "HALT", "reason": f"VIX {vix:.1f} ≥ 35 — no auto-execution"})
        result["market_regime"] = "halt"
    elif regime == "danger":
        result["risk_flags"].append({"level": "WARNING", "reason": f"VIX {vix:.1f} — elevated, widen wings by $2"})
        result["market_regime"] = "caution"
    if term_structure == "backwardation":
        result["risk_flags"].append({"level": "WARNING", "reason": "VIX backwardation — institutional hedging, reduce size"})
except Exception as e:
    print(f"[market_intelligence] VIX fetch failed: {e}")
    result["data_quality"] -= 15

# ── Fear & Greed (CNN scrape with VIX fallback) ───────────────────────
try:
    import urllib.request
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        fg_data = json.loads(resp.read())
    fg_score = int(fg_data.get("fear_and_greed", {}).get("score", 50))
    fg_label = fg_data.get("fear_and_greed", {}).get("rating", "neutral").lower()
    result["macro"]["fear_greed_score"] = fg_score
    result["macro"]["fear_greed_label"] = fg_label
    print(f"[market_intelligence] Fear & Greed: {fg_score} ({fg_label})")
    if fg_score < 20:
        result["risk_flags"].append({"level": "WARNING", "reason": f"Extreme Fear ({fg_score}) — market may gap down"})
    elif fg_score > 85:
        result["risk_flags"].append({"level": "CAUTION", "reason": f"Extreme Greed ({fg_score}) — complacency risk"})
except Exception as e:
    vix_val = result["macro"].get("vix", 20)
    fg_score = max(0, min(100, int(100 - (vix_val - 10) * 3)))
    result["macro"]["fear_greed_score"] = fg_score
    result["macro"]["fear_greed_label"] = "greed" if fg_score > 60 else "fear" if fg_score < 40 else "neutral"
    print(f"[market_intelligence] F&G scrape failed ({e}), using VIX proxy: {fg_score}")

# ── Yield curve ───────────────────────────────────────────────────────
try:
    t10 = yf.Ticker("^TNX")
    t2 = yf.Ticker("^IRX")
    h10 = t10.history(period="2d")
    h2 = t2.history(period="2d")
    y10 = float(h10["Close"].iloc[-1]) / 100 if not h10.empty else 0.0
    y2 = float(h2["Close"].iloc[-1]) / 100 if not h2.empty else 0.0
    spread = round(y10 - y2, 4)
    yc_regime = "inverted" if spread < -0.002 else "flat" if spread < 0.005 else "normal"
    result["macro"]["ten_year_yield"] = round(y10, 4)
    result["macro"]["two_year_yield"] = round(y2, 4)
    result["macro"]["yield_spread"] = spread
    result["macro"]["yield_curve"] = yc_regime
    if yc_regime == "inverted":
        result["risk_flags"].append({"level": "CAUTION", "reason": "Yield curve inverted — recession signal, reduce position size"})
    print(f"[market_intelligence] Yields: 10Y {y10:.2%} | 2Y {y2:.2%} | Spread {spread:.3f} ({yc_regime})")
except Exception as e:
    print(f"[market_intelligence] Yields failed: {e}")
    result["data_quality"] -= 5

# ── Finnhub event calendar ────────────────────────────────────────────
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
if FINNHUB_KEY:
    try:
        import urllib.request, urllib.parse
        today = datetime.now(ET).date()
        end = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/calendar/economic?token={FINNHUB_KEY}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            ev_data = json.loads(resp.read())
        events = ev_data.get("economicCalendar", [])
        fomc_days = cpi_days = jobs_days = 999
        is_event_day = False
        event_desc = ""
        for ev in events:
            try:
                ev_date = datetime.strptime(ev.get("time","")[:10], "%Y-%m-%d").date()
                days_away = (ev_date - today).days
                if days_away < 0: continue
                name = ev.get("event", "").lower()
                if any(w in name for w in ["fomc","fed","interest rate"]): fomc_days = min(fomc_days, days_away)
                if any(w in name for w in ["cpi","inflation"]): cpi_days = min(cpi_days, days_away)
                if any(w in name for w in ["nonfarm","payroll","unemployment"]): jobs_days = min(jobs_days, days_away)
                if days_away == 0:
                    is_event_day = True
                    event_desc = ev.get("event", "")
            except: continue
        result["macro"]["fomc_days_away"] = fomc_days
        result["macro"]["cpi_days_away"] = cpi_days
        result["macro"]["jobs_days_away"] = jobs_days
        result["macro"]["is_event_day"] = is_event_day
        result["macro"]["event_today"] = event_desc
        if is_event_day:
            result["risk_flags"].append({"level": "CAUTION", "reason": f"Economic event today: {event_desc}"})
        if fomc_days <= 1:
            result["risk_flags"].append({"level": "CAUTION", "reason": f"FOMC in {fomc_days} day(s) — go smaller on condors"})
        print(f"[market_intelligence] Events: FOMC {fomc_days}d | CPI {cpi_days}d | Event today: {is_event_day}")
    except Exception as e:
        print(f"[market_intelligence] Finnhub events failed: {e}")
        result["data_quality"] -= 5

# ── Symbol snapshots ──────────────────────────────────────────────────
WATCHLIST = ["SPY", "QQQ", "NVDA", "PLTR", "TSM", "AMD", "AVGO", "ASML", "MU", "SOFI", "CCJ"]

for sym in WATCHLIST:
    try:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period="60d")
        info = ticker.info
        if hist.empty:
            print(f"[market_intelligence] {sym}: no data")
            continue

        closes = hist["Close"]
        price = float(closes.iloc[-1])
        prev  = float(closes.iloc[-2]) if len(closes) > 1 else price
        chg   = round((price - prev) / prev * 100, 2) if prev > 0 else 0.0
        vol   = int(hist["Volume"].iloc[-1])
        avg_vol = int(hist["Volume"].mean())

        # Moving averages
        sma20  = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else 0.0
        ema50  = float(closes.ewm(span=50).mean().iloc[-1]) if len(closes) >= 50 else 0.0
        ema200 = float(closes.ewm(span=200).mean().iloc[-1]) if len(closes) >= 60 else 0.0

        # RSI
        delta_c = closes.diff()
        gain = delta_c.clip(lower=0).rolling(14).mean()
        loss = (-delta_c.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = round(float((100 - 100/(1+rs)).iloc[-1]), 1) if len(closes) >= 15 else 50.0

        # MACD
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_signal = "bullish" if macd_line.iloc[-1] > signal_line.iloc[-1] else "bearish"

        # Bollinger Bands
        rm = closes.rolling(20).mean()
        rs2 = closes.rolling(20).std()
        bb_upper = rm + 2*rs2
        bb_lower = rm - 2*rs2
        bb_width = round(float((bb_upper.iloc[-1]-bb_lower.iloc[-1])/rm.iloc[-1]), 4) if rm.iloc[-1]>0 else 0.0
        if price > float(bb_upper.iloc[-1]):   bb_pos = "above_upper"
        elif price < float(bb_lower.iloc[-1]): bb_pos = "below_lower"
        elif bb_width < 0.04:                  bb_pos = "squeeze"
        else:                                  bb_pos = "middle"

        # Earnings days away via Finnhub
        earn_days = 999
        if FINNHUB_KEY and sym != "SPY" and sym != "QQQ":
            try:
                today = datetime.now(ET).date()
                end_e = (today + timedelta(days=60)).strftime("%Y-%m-%d")
                eurl = f"https://finnhub.io/api/v1/calendar/earnings?from={today}&to={end_e}&symbol={sym}&token={FINNHUB_KEY}"
                req_e = urllib.request.Request(eurl)
                with urllib.request.urlopen(req_e, timeout=6) as r:
                    edata = json.loads(r.read())
                for ev in edata.get("earningsCalendar", []):
                    try:
                        ed = datetime.strptime(ev["date"], "%Y-%m-%d").date()
                        d = (ed - today).days
                        if d >= 0: earn_days = min(earn_days, d); break
                    except: pass
                time.sleep(0.3)  # rate limit
            except: pass

        snap = {
            "price": round(price, 2),
            "change_pct": chg,
            "volume": vol,
            "avg_volume": avg_vol,
            "rsi_14": rsi,
            "macd_signal": macd_signal,
            "bb_position": bb_pos,
            "bb_width": bb_width,
            "sma_20": round(sma20, 2),
            "ema_50": round(ema50, 2),
            "ema_200": round(ema200, 2),
            "above_ema200": price > ema200 if ema200 > 0 else False,
            "pe_ratio": round(float(info.get("trailingPE") or 0), 1),
            "market_cap_b": round(float(info.get("marketCap") or 0) / 1e9, 1),
            "next_earnings_days": earn_days,
        }
        result["symbols"][sym] = snap
        print(f"[market_intelligence] {sym}: ${price:.2f} ({chg:+.1f}%) RSI:{rsi} MACD:{macd_signal} BB:{bb_pos} Earn:{earn_days}d")
    except Exception as e:
        print(f"[market_intelligence] {sym} failed: {e}")
        result["data_quality"] -= 2

# ── High conviction setup screener ────────────────────────────────────
vix_val = result["macro"].get("vix", 20)
setups = []
for sym, snap in result["symbols"].items():
    if not snap.get("price"): continue
    rsi = snap.get("rsi_14", 50)
    macd = snap.get("macd_signal", "neutral")
    bb = snap.get("bb_position", "middle")
    above200 = snap.get("above_ema200", False)
    earn_days = snap.get("next_earnings_days", 999)
    if earn_days < 14: continue  # always skip earnings blackout

    conviction = 0
    reasons = []
    setup_type = None

    # Iron condor (SPY/QQQ only)
    if sym in ["SPY", "QQQ"]:
        if 13 <= vix_val <= 30:
            conviction += 25; reasons.append(f"VIX {vix_val:.1f} in condor-friendly range")
        if 35 < rsi < 65:
            conviction += 20; reasons.append(f"RSI {rsi:.0f} neutral — range-bound")
        if macd == "neutral" or bb in ["middle", "squeeze"]:
            conviction += 15; reasons.append("Low trend strength")
        if result["macro"].get("vix_term_structure") == "contango":
            conviction += 10; reasons.append("VIX contango — stable regime")
        if conviction >= 50:
            setup_type = "iron_condor"

    # Covered call (portfolio holdings)
    elif sym in ["PLTR", "TSM", "MU", "AMD", "AVGO", "ASML", "SOFI"]:
        if above200:
            conviction += 20; reasons.append("Above EMA200 — uptrend intact")
        if rsi > 55:
            conviction += 15; reasons.append(f"RSI {rsi:.0f} — momentum extended, call premium rich")
        if earn_days > 21:
            conviction += 20; reasons.append(f"Earnings {earn_days}d away — safe window")
        if macd == "bullish":
            conviction += 10; reasons.append("MACD bullish — stock supported")
        if conviction >= 50:
            setup_type = "covered_call"

    if setup_type:
        setups.append({
            "symbol": sym,
            "setup_type": setup_type,
            "conviction_score": conviction,
            "reasons": reasons,
            "price": snap.get("price"),
            "rsi": rsi,
            "earnings_days_away": earn_days,
        })

setups.sort(key=lambda x: x["conviction_score"], reverse=True)
result["high_conviction_setups"] = setups[:5]

# ── Overall market regime ─────────────────────────────────────────────
halt_flags = [f for f in result["risk_flags"] if f["level"] == "HALT"]
warn_flags  = [f for f in result["risk_flags"] if f["level"] == "WARNING"]
if halt_flags or vix_val >= 35:
    result["market_regime"] = "halt"
elif len(warn_flags) >= 2 or vix_val >= 28:
    result["market_regime"] = "risk_off"
elif warn_flags or vix_val >= 22:
    result["market_regime"] = "caution"
else:
    result["market_regime"] = "normal"

# ── Save ──────────────────────────────────────────────────────────────
out_path = f"{CACHE}/market_intelligence.json"
with open(out_path, "w") as f:
    json.dump(result, f, indent=2, default=str)

print(f"\n[market_intelligence] ✅ Done — regime={result['market_regime']} "
      f"setups={len(result['high_conviction_setups'])} "
      f"flags={len(result['risk_flags'])} "
      f"quality={result['data_quality']}/100")
print(f"[market_intelligence] Saved → {out_path}")
