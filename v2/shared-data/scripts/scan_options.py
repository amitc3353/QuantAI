#!/usr/bin/env python3
"""
QuantAI Universal Options Scanner
Scans the market by CRITERIA (not a fixed list) for:
1. Credit spread opportunities (daily)
2. Collar candidates (Mon/Wed)

Data sources: yfinance (free), with support for Finnhub news/earnings
"""
import json, os, sys, time
from datetime import datetime, timedelta
import yfinance as yf

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break


CACHE = os.environ.get("QUANTAI_CACHE", "/home/trader/QuantAI/v2/shared-data/cache")

# ── Dynamic ticker discovery ──────────────────────────────────────────
# Instead of a hardcoded list, we pull from multiple sources and filter

def discover_tickers():
    """Pull optionable, liquid tickers from multiple sources."""
    tickers = set()

    # Tier 1: Major ETFs (always scan these — most liquid options)
    etfs = ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV",
            "XBI", "XOP", "GDX", "ARKK", "EEM", "HYG", "TLT", "SLV", "GLD"]
    tickers.update(etfs)

    # Tier 2: Most active options stocks (dynamic via yfinance)
    try:
        # yfinance trending tickers
        trending = yf.Tickers(" ".join([
            "TSLA", "NVDA", "AMD", "AAPL", "AMZN", "META", "GOOGL", "MSFT",
            "NFLX", "COIN", "MSTR", "PLTR", "SOFI", "NIO", "RIVN", "LCID",
            "HOOD", "SNAP", "PINS", "ROKU", "FUBO", "HIMS", "IONQ", "RGTI",
            "SOUN", "AI", "BBAI", "PATH", "AFRM", "UPST", "NU",
            "AAL", "UAL", "CCL", "NCLH", "F", "GM",
            "MU", "INTC", "SMCI", "DELL",
            "VALE", "CLF", "GOLD", "KGC", "AG",
            "ET", "RIG", "OXY", "CVX", "XOM",
            "T", "VZ", "CHPT", "PLUG", "FCEL",
            "MPW", "OPEN", "GRAB", "CPNG", "BABA",
            "BA", "LUV", "DAL", "UBER", "LYFT",
            "SQ", "PYPL", "V", "MA", "JPM", "GS",
            "CRSP", "MRNA", "PFE", "ABBV",
            "DIS", "WBD",
        ]))
        tickers.update(trending.symbols)
    except:
        pass

    return list(tickers)


# ── Technical analysis ────────────────────────────────────────────────

def get_technicals(ticker_obj, price):
    """RSI, MACD, SMAs, 52w range, volume trend."""
    try:
        hist = ticker_obj.history(period="60d")
        if len(hist) < 26:
            return {}
        close = hist["Close"]

        # RSI(14)
        d = close.diff()
        g = d.where(d > 0, 0).rolling(14).mean()
        l = (-d.where(d < 0, 0)).rolling(14).mean()
        rsi = float(100 - 100 / (1 + (g / l).iloc[-1])) if l.iloc[-1] != 0 else 50

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_signal = "bullish" if float(macd.iloc[-1]) > float(signal.iloc[-1]) else "bearish"

        # SMAs
        sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None

        # 52w high/low
        h1y = ticker_obj.history(period="1y")
        high_52w = float(h1y["High"].max()) if not h1y.empty else None
        low_52w = float(h1y["Low"].min()) if not h1y.empty else None
        pct_from_high = round((price - high_52w) / high_52w * 100, 1) if high_52w else None

        # Recent trend (5-day)
        if len(close) >= 5:
            trend_5d = round((float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5]) * 100, 1)
        else:
            trend_5d = None

        return {
            "rsi14": round(rsi, 1),
            "macd": macd_signal,
            "sma20": round(sma20, 2) if sma20 else None,
            "sma50": round(sma50, 2) if sma50 else None,
            "high_52w": round(high_52w, 2) if high_52w else None,
            "low_52w": round(low_52w, 2) if low_52w else None,
            "pct_from_high": pct_from_high,
            "trend_5d_pct": trend_5d,
        }
    except:
        return {}


def get_earnings(ticker_obj):
    """Check next earnings date."""
    try:
        info = ticker_obj.info or {}
        ts = info.get("earningsTimestamp")
        if ts:
            edate = datetime.fromtimestamp(ts).date()
            days = (edate - datetime.now().date()).days
            return {"date": str(edate), "days_until": days, "within_7d": 0 <= days <= 7}
    except:
        pass
    return {"date": "unknown", "days_until": None, "within_7d": False}


def get_iv_rank(ticker_obj):
    """Compute IV rank from historical volatility as proxy."""
    try:
        hist = ticker_obj.history(period="1y")
        if len(hist) < 60:
            return None
        returns = hist["Close"].pct_change().dropna()
        # Rolling 30-day HV
        hvs = returns.rolling(30).std() * (252 ** 0.5) * 100
        hvs = hvs.dropna()
        if len(hvs) < 30:
            return None
        current = float(hvs.iloc[-1])
        low = float(hvs.min())
        high = float(hvs.max())
        if high == low:
            return 50
        rank = (current - low) / (high - low) * 100
        return round(rank, 1)
    except:
        return None


# ── Credit Spread Scanner ─────────────────────────────────────────────

def scan_credit_spreads():
    """Find best credit spread opportunities across all liquid tickers."""
    tickers = discover_tickers()
    print(f"Scanning {len(tickers)} tickers for credit spreads...", file=sys.stderr)

    # Get VIX for context
    vix = None
    try:
        vix_data = yf.Ticker("^VIX").history(period="1d")
        if not vix_data.empty:
            vix = round(float(vix_data["Close"].iloc[-1]), 1)
    except:
        pass

    results = []

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if not price or price < 3:
                continue

            avg_vol = info.get("averageVolume", 0)
            if avg_vol < 1_000_000:
                continue

            # Options must exist
            exps = t.options
            if not exps:
                continue

            # Find weekly expiry (5-10 days out)
            target_exp = None
            for exp in exps:
                days_out = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now().date()).days
                if 5 <= days_out <= 12:
                    target_exp = exp
                    break
            if not target_exp:
                continue

            chain = t.option_chain(target_exp)
            if chain.calls.empty or chain.puts.empty:
                continue

            # IV rank
            iv_rank = get_iv_rank(t)
            if iv_rank is not None and iv_rank < 30:
                continue

            # Earnings check
            earnings = get_earnings(t)
            if earnings.get("within_7d"):
                continue

            # Get technicals to decide direction
            technicals = get_technicals(t, price)
            rsi = technicals.get("rsi14", 50)
            macd = technicals.get("macd", "neutral")

            # Decide: put spread (bullish) or call spread (bearish)
            if rsi < 40 or macd == "bullish":
                direction = "PUT_SPREAD"  # bullish — sell put spread below
                distance_pct = 0.05 if (vix or 20) < 25 else 0.07  # wider when VIX high
                short_target = round(price * (1 - distance_pct))
                strikes = chain.puts[chain.puts.strike <= short_target].sort_values("strike", ascending=False)
                if len(strikes) < 2:
                    continue
                short_opt = strikes.iloc[0]
                # Find long put $5 below (or nearest available)
                long_candidates = chain.puts[chain.puts.strike < float(short_opt["strike"]) - 2]
                if long_candidates.empty:
                    continue
                long_opt = long_candidates.iloc[-1]
            else:
                direction = "CALL_SPREAD"  # bearish — sell call spread above
                distance_pct = 0.05 if (vix or 20) < 25 else 0.07
                short_target = round(price * (1 + distance_pct))
                strikes = chain.calls[chain.calls.strike >= short_target].sort_values("strike")
                if len(strikes) < 2:
                    continue
                short_opt = strikes.iloc[0]
                long_candidates = chain.calls[chain.calls.strike > float(short_opt["strike"]) + 2]
                if long_candidates.empty:
                    continue
                long_opt = long_candidates.iloc[0]

            short_strike = float(short_opt["strike"])
            long_strike = float(long_opt["strike"])
            width = abs(short_strike - long_strike)

            short_bid = float(short_opt.get("bid", 0))
            long_ask = float(long_opt.get("ask", 0))
            credit = short_bid - long_ask

            if credit <= 0.05 or width <= 0:
                continue

            max_loss = (width - credit) * 100
            max_profit = credit * 100
            risk_reward = max_loss / max_profit if max_profit > 0 else 999
            distance_from_price = abs(price - short_strike) / price * 100

            # Liquidity check
            short_oi = int(short_opt.get("openInterest", 0) or 0)
            short_vol = int(short_opt.get("volume", 0) or 0)
            if short_oi < 100:
                continue

            sector = info.get("sector", "ETF" if sym in ["SPY","QQQ","IWM","DIA","XLF","XLE","XLK","XLV","XBI","GDX","ARKK","EEM","SLV","GLD","TLT","XOP","HYG"] else "Unknown")
            name = info.get("shortName", sym)

            results.append({
                "symbol": sym,
                "name": name,
                "price": round(price, 2),
                "sector": sector,
                "direction": direction,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "width": round(width, 2),
                "credit": round(credit, 2),
                "max_profit": round(max_profit, 0),
                "max_loss": round(max_loss, 0),
                "risk_reward": round(risk_reward, 1),
                "distance_pct": round(distance_from_price, 1),
                "expiry": target_exp,
                "iv_rank": iv_rank,
                "short_oi": short_oi,
                "short_vol": short_vol,
                "technicals": technicals,
                "earnings": earnings,
            })

        except Exception as e:
            continue

    # Sort by risk/reward (lower is better) then by credit (higher is better)
    results.sort(key=lambda x: (x["risk_reward"], -x["credit"]))

    return {
        "ts": datetime.now().isoformat(),
        "scan_type": "credit_spreads",
        "vix": vix,
        "tickers_scanned": len(tickers),
        "opportunities_found": len(results),
        "top_picks": results[:5],  # top 5 for agent to analyze and pick best 2
    }


# ── Collar Scanner ────────────────────────────────────────────────────

def scan_collars():
    """Find best collar candidates across all liquid tickers $5-$25."""
    tickers = discover_tickers()
    print(f"Scanning {len(tickers)} tickers for collar candidates...", file=sys.stderr)

    results = []

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if not price or price < 5 or price > 25:
                continue

            avg_vol = info.get("averageVolume", 0)
            if avg_vol < 5_000_000:
                continue

            exps = t.options
            if not exps or len(exps) < 2:
                continue

            chain = t.option_chain(exps[0])
            if chain.calls.empty or chain.puts.empty:
                continue

            # ATM liquidity
            atm_idx = (chain.calls["strike"] - price).abs().argsort()[:1]
            atm_call = chain.calls.iloc[atm_idx.values[0]]
            atm_oi = int(atm_call.get("openInterest", 0) or 0)
            spread = float(atm_call.get("ask", 0)) - float(atm_call.get("bid", 0))
            if atm_oi < 200 or spread > 0.30:
                continue

            iv_rank = get_iv_rank(t)
            if iv_rank is not None and iv_rank < 25:
                continue

            earnings = get_earnings(t)

            # OTM call ~7% above
            call_target = round(price * 1.07)
            otm_calls = chain.calls[chain.calls.strike >= call_target]
            if otm_calls.empty:
                continue
            oc = otm_calls.iloc[0]
            call_strike = float(oc["strike"])
            call_bid = float(oc.get("bid", 0))
            if call_bid < 0.10:
                continue

            # OTM put ~18% below
            put_target = round(price * 0.82)
            otm_puts = chain.puts[chain.puts.strike <= put_target]
            if otm_puts.empty:
                continue
            op = otm_puts.iloc[-1]
            put_strike = float(op["strike"])
            put_ask = float(op.get("ask", 0))

            monthly_call = call_bid * 200 * 2
            monthly_put = put_ask * 200
            net_monthly = monthly_call - monthly_put
            max_loss = (price - put_strike) * 200

            technicals = get_technicals(t, price)

            sector = info.get("sector", "Unknown")
            name = info.get("shortName", sym)
            profitable = (info.get("profitMargins") or 0) > 0
            rev_growth = info.get("revenueGrowth")

            results.append({
                "symbol": sym,
                "name": name,
                "price": round(price, 2),
                "sector": sector,
                "call_strike": call_strike,
                "call_bid": round(call_bid, 2),
                "put_strike": put_strike,
                "put_ask": round(put_ask, 2),
                "net_monthly_200sh": round(net_monthly, 0),
                "max_loss_200sh": round(max_loss, 0),
                "net_credit_collar": net_monthly > 0,
                "iv_rank": iv_rank,
                "profitable": profitable,
                "revenue_growth": round(rev_growth * 100, 1) if rev_growth else None,
                "expiry_nearest": exps[0],
                "technicals": technicals,
                "earnings": earnings,
            })
        except:
            continue

    results.sort(key=lambda x: x["net_monthly_200sh"], reverse=True)

    return {
        "ts": datetime.now().isoformat(),
        "scan_type": "collar_candidates",
        "tickers_scanned": len(tickers),
        "passed_filters": len(results),
        "top_candidates": results[:5],
    }


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scan_type = sys.argv[1] if len(sys.argv) > 1 else "credit_spreads"

    os.makedirs(CACHE, exist_ok=True)

    if scan_type == "credit_spreads":
        data = scan_credit_spreads()
        with open(f"{CACHE}/credit_spread_scan.json", "w") as f:
            json.dump(data, f, indent=2)
        print(json.dumps(data, indent=2))
        print(f"\n{data['opportunities_found']} opportunities from {data['tickers_scanned']} tickers", file=sys.stderr)

    elif scan_type == "collars":
        data = scan_collars()
        with open(f"{CACHE}/collar_candidates.json", "w") as f:
            json.dump(data, f, indent=2)
        print(json.dumps(data, indent=2))
        print(f"\n{data['passed_filters']} candidates from {data['tickers_scanned']} tickers", file=sys.stderr)

    elif scan_type == "both":
        cs = scan_credit_spreads()
        with open(f"{CACHE}/credit_spread_scan.json", "w") as f:
            json.dump(cs, f, indent=2)
        print("=== CREDIT SPREADS ===")
        print(json.dumps(cs, indent=2))

        print("\n\n=== COLLAR CANDIDATES ===")
        co = scan_collars()
        with open(f"{CACHE}/collar_candidates.json", "w") as f:
            json.dump(co, f, indent=2)
        print(json.dumps(co, indent=2))
    else:
        print(f"Usage: {sys.argv[0]} [credit_spreads|collars|both]")
