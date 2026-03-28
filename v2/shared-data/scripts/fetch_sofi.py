#!/usr/bin/env python3
"""Fetch SOFI data — used by Research agent and cron jobs."""
import json, os
from datetime import datetime
import yfinance as yf

CACHE = os.environ.get("QUANTAI_HOME", "/root/quantai-v2") + "/shared-data/cache"

def fetch():
    sofi = yf.Ticker("SOFI")
    hist = sofi.history(period="5d")
    if hist.empty:
        return {"error": "No data (market closed?)"}

    p = float(hist.iloc[-1]["Close"])
    prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else p
    chg = p - prev

    # RSI(14)
    h60 = sofi.history(period="60d")
    d = h60["Close"].diff()
    g = d.where(d > 0, 0).rolling(14).mean()
    l = (-d.where(d < 0, 0)).rolling(14).mean()
    rsi = float(100 - 100 / (1 + (g / l).iloc[-1])) if l.iloc[-1] != 0 else 50

    # SMAs
    sma50 = float(h60["Close"].rolling(50).mean().iloc[-1]) if len(h60) >= 50 else None
    h1y = sofi.history(period="1y")
    sma200 = float(h1y["Close"].rolling(200).mean().iloc[-1]) if len(h1y) >= 200 else None

    # Options chain
    opts = {}
    try:
        exps = sofi.options
        if exps:
            ch = sofi.option_chain(exps[0])
            c16 = ch.calls[ch.calls.strike == 16.0]
            if not c16.empty:
                r = c16.iloc[0]
                opts["call_16"] = {
                    "bid": float(r.get("bid", 0)), "ask": float(r.get("ask", 0)),
                    "iv": float(r.get("impliedVolatility", 0)),
                    "oi": int(r.get("openInterest", 0) or 0),
                    "volume": int(r.get("volume", 0) or 0),
                    "expiry": exps[0],
                }
            p12 = ch.puts[ch.puts.strike == 12.0]
            if not p12.empty:
                r = p12.iloc[0]
                opts["put_12"] = {
                    "bid": float(r.get("bid", 0)), "ask": float(r.get("ask", 0)),
                    "iv": float(r.get("impliedVolatility", 0)),
                    "oi": int(r.get("openInterest", 0) or 0),
                    "volume": int(r.get("volume", 0) or 0),
                    "expiry": exps[0],
                }
            atm = ch.calls.iloc[(ch.calls.strike - p).abs().argsort()[:1]]
            if not atm.empty:
                opts["atm_iv"] = float(atm.iloc[0].get("impliedVolatility", 0))
    except Exception as e:
        opts["error"] = str(e)

    info = sofi.info or {}
    result = {
        "ts": datetime.now().isoformat(),
        "symbol": "SOFI",
        "price": round(p, 2),
        "change": round(chg, 2),
        "change_pct": round(chg / prev * 100, 2) if prev else 0,
        "volume": int(hist.iloc[-1]["Volume"]),
        "avg_vol": int(info.get("averageVolume", 0)),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi14": round(rsi, 1),
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE"),
        "to_call": round(16.0 - p, 2),
        "to_put": round(p - 12.0, 2),
        "options": opts,
    }

    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(f"{CACHE}/sofi_history", exist_ok=True)
    with open(f"{CACHE}/sofi_latest.json", "w") as f:
        json.dump(result, f, indent=2)
    with open(f"{CACHE}/sofi_history/{datetime.now():%Y-%m-%d}.json", "w") as f:
        json.dump(result, f, indent=2)
    return result

if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2))
