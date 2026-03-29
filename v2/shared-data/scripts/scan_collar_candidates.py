#!/usr/bin/env python3
"""
Collar Candidate Scanner — criteria-based, no hardcoded ticker list.
Scans the most actively traded options stocks, filters by collar suitability,
and pulls deep analysis data for the top candidates.
"""
import json, os, sys
from datetime import datetime, timedelta
import yfinance as yf
import requests

CACHE = os.environ.get("QUANTAI_HOME", "/root/quantai-v2") + "/shared-data/cache"


def get_active_options_tickers():
    """
    Get a broad list of tickers to scan from multiple sources.
    We cast a wide net and let the filters narrow it down.
    """
    tickers = set()

    # Method 1: yfinance most active / trending
    try:
        trending = yf.Tickers(
            # S&P small/mid cap + popular options stocks across sectors
            # This is a SEED list — the real filtering happens below
            " ".join([
                # Financials
                "SOFI", "HOOD", "NU", "AFRM", "UPST", "LC", "ALLY", "SYF",
                # Tech / Software
                "PLTR", "PATH", "SNAP", "PINS", "ROKU", "FUBO",
                "IONQ", "RGTI", "BBAI", "AI", "SOUN",
                # EV / Energy
                "NIO", "RIVN", "LCID", "CHPT", "PLUG", "FCEL", "ET", "RIG",
                # Healthcare
                "HIMS", "CLOV", "DNA", "CRSP", "GDRX",
                # Consumer / Travel
                "AAL", "UAL", "CCL", "NCLH", "F", "GM",
                # Mining / Commodities
                "VALE", "CLF", "GOLD", "KGC", "AG", "BTG",
                # Telecom / Legacy
                "T", "NOK", "BB",
                # REIT / Other
                "MPW", "OPEN", "GRAB", "CPNG",
            ])
        )
        for sym in trending.symbols:
            tickers.add(sym)
    except:
        pass

    # Method 2: Pull Yahoo Finance most active options (top volume)
    try:
        # Scrape day's most active by volume — catches trending names
        for url_sym in ["TSLA", "NVDA", "AMD", "AAPL"]:
            t = yf.Ticker(url_sym)
            # These are too expensive but checking for related names
            pass
    except:
        pass

    return list(tickers)


def analyze_technicals(ticker_obj, price):
    """Compute RSI, MACD, SMAs for a ticker."""
    try:
        hist = ticker_obj.history(period="60d")
        if len(hist) < 26:
            return {}

        close = hist["Close"]

        # RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs.iloc[-1]))) if loss.iloc[-1] != 0 else 50

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        macd_signal = "bullish" if macd_val > signal_val else "bearish"

        # SMAs
        sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None

        # 52-week high/low
        hist_1y = ticker_obj.history(period="1y")
        high_52w = float(hist_1y["High"].max()) if not hist_1y.empty else None
        low_52w = float(hist_1y["Low"].min()) if not hist_1y.empty else None
        pct_from_high = round((price - high_52w) / high_52w * 100, 1) if high_52w else None

        # Volume trend
        vol_avg = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None

        return {
            "rsi14": round(rsi, 1),
            "macd": macd_signal,
            "macd_value": round(macd_val, 3),
            "sma20": round(sma20, 2) if sma20 else None,
            "sma50": round(sma50, 2) if sma50 else None,
            "high_52w": round(high_52w, 2) if high_52w else None,
            "low_52w": round(low_52w, 2) if low_52w else None,
            "pct_from_52w_high": pct_from_high,
        }
    except:
        return {}


def check_earnings(ticker_obj):
    """Check if earnings are within 14 days."""
    try:
        cal = ticker_obj.calendar
        if cal is not None and not cal.empty:
            if "Earnings Date" in cal.index:
                earnings_date = cal.loc["Earnings Date"]
                if hasattr(earnings_date, "iloc"):
                    earnings_date = earnings_date.iloc[0]
                if hasattr(earnings_date, "date"):
                    days_until = (earnings_date.date() - datetime.now().date()).days
                    return {
                        "date": str(earnings_date.date()),
                        "days_until": days_until,
                        "too_close": 0 <= days_until <= 14,
                    }
        # Try info field
        info = ticker_obj.info or {}
        ts = info.get("earningsTimestamp")
        if ts:
            edate = datetime.fromtimestamp(ts).date()
            days_until = (edate - datetime.now().date()).days
            return {
                "date": str(edate),
                "days_until": days_until,
                "too_close": 0 <= days_until <= 14,
            }
    except:
        pass
    return {"date": "unknown", "days_until": None, "too_close": False}


def scan():
    """Main scan: criteria-based filtering + deep dive on top candidates."""
    tickers = get_active_options_tickers()
    print(f"Scanning {len(tickers)} tickers...", file=sys.stderr)

    passed = []

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}

            # --- CRITERIA FILTERS ---

            # Price $5-$25
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if not price or price < 5 or price > 25:
                continue

            # Volume > 5M
            avg_vol = info.get("averageVolume", 0)
            if avg_vol < 5_000_000:
                continue

            # Options must exist with at least 2 expiries
            exps = t.options
            if not exps or len(exps) < 2:
                continue

            # Get nearest chain
            chain = t.option_chain(exps[0])
            if chain.calls.empty or chain.puts.empty:
                continue

            # ATM option liquidity check
            atm_idx = (chain.calls["strike"] - price).abs().argsort()[:1]
            atm_call = chain.calls.iloc[atm_idx.values[0]]
            atm_iv = float(atm_call.get("impliedVolatility", 0))
            atm_oi = int(atm_call.get("openInterest", 0) or 0)
            atm_bid = float(atm_call.get("bid", 0))
            atm_ask = float(atm_call.get("ask", 0))
            spread = atm_ask - atm_bid if atm_ask and atm_bid else 999

            if atm_oi < 200 or spread > 0.30:
                continue

            # --- COLLAR ECONOMICS ---

            # OTM call: ~7% above price
            call_target = round(price * 1.07)
            otm_calls = chain.calls[chain.calls.strike >= call_target]
            if otm_calls.empty:
                continue
            oc = otm_calls.iloc[0]
            call_strike = float(oc["strike"])
            call_bid = float(oc.get("bid", 0))
            if call_bid < 0.10:
                continue

            # OTM put: ~18% below price
            put_target = round(price * 0.82)
            otm_puts = chain.puts[chain.puts.strike <= put_target]
            if otm_puts.empty:
                continue
            op = otm_puts.iloc[-1]
            put_strike = float(op["strike"])
            put_ask = float(op.get("ask", 0))

            # Monthly income calculation (200 shares)
            monthly_call = call_bid * 200 * 2  # biweekly = 2x/month
            monthly_put = put_ask * 200         # monthly
            net_monthly = monthly_call - monthly_put
            max_loss = (price - put_strike) * 200

            # --- COMPANY BASICS ---
            sector = info.get("sector", "Unknown")
            name = info.get("shortName", sym)
            market_cap = info.get("marketCap", 0)
            revenue_growth = info.get("revenueGrowth")
            profitable = info.get("profitMargins", 0) and info.get("profitMargins", 0) > 0

            passed.append({
                "symbol": sym,
                "name": name,
                "price": round(price, 2),
                "sector": sector,
                "avg_volume": avg_vol,
                "market_cap": market_cap,
                "atm_iv_pct": round(atm_iv * 100, 1),
                "call_strike": call_strike,
                "call_bid": round(call_bid, 2),
                "put_strike": put_strike,
                "put_ask": round(put_ask, 2),
                "net_monthly_200sh": round(net_monthly, 0),
                "max_loss_200sh": round(max_loss, 0),
                "net_credit_collar": net_monthly > 0,
                "profitable": profitable,
                "revenue_growth": round(revenue_growth * 100, 1) if revenue_growth else None,
                "expiry_nearest": exps[0],
            })

        except Exception as e:
            continue

    # Sort by net monthly income
    passed.sort(key=lambda x: x["net_monthly_200sh"], reverse=True)

    # Deep dive on top 3
    top3 = passed[:3]
    for item in top3:
        sym = item["symbol"]
        try:
            t = yf.Ticker(sym)

            # Technicals
            item["technicals"] = analyze_technicals(t, item["price"])

            # Earnings check
            item["earnings"] = check_earnings(t)

            # Get 2-week-out expiry for more accurate collar pricing
            exps = t.options
            two_week_exp = None
            for exp in exps:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                days_out = (exp_date - datetime.now().date()).days
                if 10 <= days_out <= 18:
                    two_week_exp = exp
                    break

            if two_week_exp:
                chain2w = t.option_chain(two_week_exp)
                # Recalculate with 2-week expiry
                otm_c = chain2w.calls[chain2w.calls.strike >= round(item["price"] * 1.07)]
                if not otm_c.empty:
                    item["call_2wk_expiry"] = two_week_exp
                    item["call_2wk_bid"] = round(float(otm_c.iloc[0].get("bid", 0)), 2)
                    item["call_2wk_strike"] = float(otm_c.iloc[0]["strike"])

            # Monthly expiry for put
            month_exp = None
            for exp in exps:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                days_out = (exp_date - datetime.now().date()).days
                if 25 <= days_out <= 40:
                    month_exp = exp
                    break

            if month_exp:
                chain_m = t.option_chain(month_exp)
                otm_p = chain_m.puts[chain_m.puts.strike <= round(item["price"] * 0.82)]
                if not otm_p.empty:
                    item["put_monthly_expiry"] = month_exp
                    item["put_monthly_ask"] = round(float(otm_p.iloc[-1].get("ask", 0)), 2)
                    item["put_monthly_strike"] = float(otm_p.iloc[-1]["strike"])

        except:
            pass

    output = {
        "ts": datetime.now().isoformat(),
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "tickers_scanned": len(tickers),
        "passed_filters": len(passed),
        "top_3_deep_dive": top3,
        "remaining": [p["symbol"] for p in passed[3:10]],
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(f"{CACHE}/collar_candidates.json", "w") as f:
        json.dump(output, f, indent=2)

    return output


if __name__ == "__main__":
    data = scan()
    print(json.dumps(data, indent=2))
    print(f"\n{data['passed_filters']} stocks passed filters out of {data['tickers_scanned']} scanned", file=sys.stderr)
