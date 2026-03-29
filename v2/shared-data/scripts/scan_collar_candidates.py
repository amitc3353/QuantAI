#!/usr/bin/env python3
"""Scan for collar-worthy stocks. Used by Research agent on Mondays."""
import json, os
from datetime import datetime
import yfinance as yf

CACHE = os.environ.get("QUANTAI_HOME", "/root/quantai-v2") + "/shared-data/cache"

CANDIDATES = [
    "SOFI", "PLTR", "NIO", "RIVN", "LCID", "HOOD",
    "GRAB", "NU", "OPEN", "F", "T", "SNAP", "PINS",
    "ROKU", "PATH", "IONQ", "RGTI", "CHPT", "PLUG",
    "CLF", "AAL", "UAL", "CCL", "NCLH", "RIG", "ET",
    "VALE", "GOLD", "KGC", "AG", "BTG", "FUBO", "HIMS",
    "DNA", "FCEL", "MPW", "NOK", "BB", "MUX", "CLOV",
]

def scan():
    results = []
    for sym in CANDIDATES:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if not price or price < 5 or price > 25:
                continue

            avg_vol = info.get("averageVolume", 0)
            if avg_vol < 5_000_000:
                continue

            # Check options exist
            exps = t.options
            if not exps or len(exps) < 2:
                continue

            # Get ATM IV from nearest chain
            chain = t.option_chain(exps[0])
            if chain.calls.empty:
                continue

            atm_idx = (chain.calls["strike"] - price).abs().argsort()[:1]
            atm_call = chain.calls.iloc[atm_idx.values[0]]
            atm_iv = float(atm_call.get("impliedVolatility", 0))

            # Check liquidity
            atm_oi = int(atm_call.get("openInterest", 0) or 0)
            atm_bid = float(atm_call.get("bid", 0))
            atm_ask = float(atm_call.get("ask", 0))
            spread = atm_ask - atm_bid if atm_ask and atm_bid else 999
            if atm_oi < 200 or spread > 0.30:
                continue

            # Find OTM call (~7-10% above price) for selling
            call_strike_target = round(price * 1.07, 0)
            otm_calls = chain.calls[chain.calls.strike >= call_strike_target]
            call_premium = 0
            call_strike = 0
            if not otm_calls.empty:
                oc = otm_calls.iloc[0]
                call_premium = float(oc.get("bid", 0))
                call_strike = float(oc["strike"])

            # Find OTM put (~15-20% below price) for insurance
            put_strike_target = round(price * 0.82, 0)
            if not chain.puts.empty:
                otm_puts = chain.puts[chain.puts.strike <= put_strike_target]
                put_cost = 0
                put_strike = 0
                if not otm_puts.empty:
                    op = otm_puts.iloc[-1]  # closest to target
                    put_cost = float(op.get("ask", 0))
                    put_strike = float(op["strike"])
                else:
                    continue
            else:
                continue

            # Calculate collar economics for 200 shares
            # Biweekly calls = 2x/month, monthly puts = 1x/month
            monthly_call_income = call_premium * 200 * 2
            monthly_put_cost = put_cost * 200
            net_monthly = monthly_call_income - monthly_put_cost
            max_loss = (price - put_strike) * 200 if put_strike else price * 200

            sector = info.get("sector", "Unknown")
            name = info.get("shortName", sym)

            results.append({
                "symbol": sym,
                "name": name,
                "price": round(price, 2),
                "sector": sector,
                "avg_volume": avg_vol,
                "atm_iv": round(atm_iv * 100, 1),
                "call_strike": call_strike,
                "call_premium": round(call_premium, 2),
                "put_strike": put_strike,
                "put_cost": round(put_cost, 2),
                "net_monthly_200sh": round(net_monthly, 0),
                "max_loss_200sh": round(max_loss, 0),
                "net_credit_collar": net_monthly > 0,
                "expiry_checked": exps[0],
            })
        except Exception as e:
            continue

    # Sort by net monthly income descending
    results.sort(key=lambda x: x["net_monthly_200sh"], reverse=True)

    output = {
        "ts": datetime.now().isoformat(),
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "candidates_scanned": len(CANDIDATES),
        "passed_filters": len(results),
        "results": results[:10],  # top 10
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(f"{CACHE}/collar_candidates.json", "w") as f:
        json.dump(output, f, indent=2)

    return output

if __name__ == "__main__":
    print(json.dumps(scan(), indent=2))
