#!/usr/bin/env python3
"""
QuantAI Universal Options Scanner
Scans for:
1. Credit spreads (bull put, bear call) — daily
2. Diagonal spreads (poor man's covered call/put) — daily
3. Iron condor candidates — daily
4. Collar candidates — Mon/Wed

All strategies collect premium. No shares required.
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

CACHE = "/root/quantai-v2/shared-data/cache"
os.makedirs(CACHE, exist_ok=True)

# ── Ticker universe ───────────────────────────────────────────────────
def get_price(ticker_obj, sym):
    """Get current price — tries fast_info first (no 404s), falls back to info."""
    try:
        fi = ticker_obj.fast_info
        price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
        if price and price > 0:
            return float(price)
    except:
        pass
    try:
        info = ticker_obj.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
        if price and float(price) > 0:
            return float(price)
    except:
        pass
    return None

def get_avg_volume(ticker_obj):
    """Get average volume without triggering fundamentals 404s."""
    try:
        fi = ticker_obj.fast_info
        vol = getattr(fi, "three_month_average_volume", None)
        if vol:
            return float(vol)
    except:
        pass
    try:
        info = ticker_obj.info or {}
        return float(info.get("averageVolume", 0) or 0)
    except:
        return 0
    tickers = set()
    etfs = ["SPY","QQQ","IWM","DIA","XLF","XLE","XLK","XLV",
            "XBI","XOP","GDX","ARKK","EEM","HYG","TLT","SLV","GLD"]
    tickers.update(etfs)
    try:
        trending = yf.Tickers(" ".join([
            "TSLA","NVDA","AMD","AAPL","AMZN","META","GOOGL","MSFT",
            "NFLX","COIN","MSTR","PLTR","SOFI","NIO","RIVN",
            "HOOD","SNAP","PINS","ROKU","HIMS","IONQ",
            "SOUN","AI","BBAI","PATH","AFRM","UPST","NU",
            "AAL","UAL","CCL","NCLH","F","GM",
            "MU","INTC","SMCI","DELL","AVGO","TSM","ASML",
            "VALE","CLF","GOLD","AG",
            "ET","RIG","OXY","CVX","XOM",
            "T","VZ","CHPT","PLUG",
            "BA","LUV","DAL","UBER","LYFT",
            "SQ","PYPL","V","MA","JPM","GS",
            "CRSP","MRNA","PFE","ABBV",
            "DIS","WBD",
        ]))
        tickers.update(trending.symbols)
    except:
        pass
    return list(tickers)

# ── Technicals ────────────────────────────────────────────────────────
def get_technicals(ticker_obj, price):
    try:
        hist = ticker_obj.history(period="60d")
        if len(hist) < 26:
            return {}
        close = hist["Close"]
        d = close.diff()
        g = d.where(d > 0, 0).rolling(14).mean()
        l = (-d.where(d < 0, 0)).rolling(14).mean()
        rsi = float(100 - 100 / (1 + g / l.replace(0, 1e-10))).iloc[-1] if len(g) > 0 else 50
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9).mean()
        macd_signal = "bullish" if float(macd_line.iloc[-1]) > float(signal.iloc[-1]) else "bearish"
        ema200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else float(close.mean())
        above_ema200 = price > ema200
        return {"rsi14": round(rsi, 1), "macd": macd_signal, "above_ema200": above_ema200, "ema200": round(ema200, 2)}
    except:
        return {}

def get_iv_rank(ticker_obj):
    try:
        hist = ticker_obj.history(period="252d")
        if len(hist) < 30:
            return None
        close = hist["Close"]
        returns = close.pct_change().dropna()
        hv_current = float(returns.tail(21).std() * (252**0.5) * 100)
        hv_52w_high = float(returns.rolling(21).std().max() * (252**0.5) * 100)
        hv_52w_low  = float(returns.rolling(21).std().min() * (252**0.5) * 100)
        if hv_52w_high == hv_52w_low:
            return 50
        return round((hv_current - hv_52w_low) / (hv_52w_high - hv_52w_low) * 100, 1)
    except:
        return None

def get_earnings(ticker_obj):
    try:
        cal = ticker_obj.calendar
        if cal is None or cal.empty:
            return {"within_7d": False, "within_14d": False, "days_away": 999}
        edate = cal.columns[0] if hasattr(cal, "columns") else None
        if edate:
            days = (edate.date() - datetime.now().date()).days
            return {"within_7d": days <= 7, "within_14d": days <= 14, "days_away": days}
    except:
        pass
    return {"within_7d": False, "within_14d": False, "days_away": 999}

# ── VIX ───────────────────────────────────────────────────────────────
def get_vix():
    try:
        return float(yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1])
    except:
        return 20.0

def discover_tickers():
    """Pull liquid, optionable tickers from multiple sources."""
    tickers = set()
    etfs = ["SPY","QQQ","IWM","DIA","XLF","XLE","XLK","XLV",
            "XBI","XOP","GDX","ARKK","EEM","HYG","TLT","SLV","GLD"]
    tickers.update(etfs)
    try:
        stocks = [
            "TSLA","NVDA","AMD","AAPL","AMZN","META","GOOGL","MSFT",
            "NFLX","COIN","MSTR","PLTR","SOFI","NIO","RIVN",
            "HOOD","SNAP","PINS","ROKU","HIMS","IONQ",
            "SOUN","AI","BBAI","PATH","AFRM","UPST","NU",
            "AAL","UAL","CCL","NCLH","F","GM",
            "MU","INTC","SMCI","DELL","AVGO","TSM","ASML",
            "VALE","CLF","GOLD","AG",
            "ET","RIG","OXY","CVX","XOM",
            "T","VZ","CHPT","PLUG",
            "BA","LUV","DAL","UBER","LYFT",
            "SQ","PYPL","V","MA","JPM","GS",
            "CRSP","MRNA","PFE","ABBV",
            "DIS","WBD",
        ]
        tickers.update(stocks)
    except:
        pass
    return list(tickers)

# ── Credit spread scan ────────────────────────────────────────────────
def scan_credit_spreads(tickers, vix):
    opportunities = []
    print(f"Scanning {len(tickers)} tickers for credit spreads...")

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            price = get_price(t, sym)
            if not price or price < 3:
                continue
            if get_avg_volume(t) < 5_000_000:
                continue

            exps = t.options
            if not exps:
                continue

            target_exp = None
            for exp in exps:
                days_out = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now().date()).days
                if 5 <= days_out <= 21:
                    target_exp = exp
                    break
            if not target_exp:
                continue

            chain = t.option_chain(target_exp)
            if chain.calls.empty or chain.puts.empty:
                continue

            # OI gate
            atm_puts  = chain.puts[abs(chain.puts.strike - price) / price < 0.05]
            atm_calls = chain.calls[abs(chain.calls.strike - price) / price < 0.05]
            if int(atm_puts["openInterest"].max() if not atm_puts.empty else 0) < 200 and \
               int(atm_calls["openInterest"].max() if not atm_calls.empty else 0) < 200:
                continue

            iv_rank = get_iv_rank(t)
            if iv_rank is not None and iv_rank < 30:
                continue

            earnings = get_earnings(t)
            if earnings.get("within_14d"):
                continue

            tech = get_technicals(t, price)
            rsi = tech.get("rsi14", 50)
            macd = tech.get("macd", "neutral")
            above_ema = tech.get("above_ema200", True)

            # Pick direction
            if rsi < 45 and above_ema:
                direction = "bull_put_spread"
                dist = 0.05 if vix < 25 else 0.07
                short_target = round(price * (1 - dist))
                strikes = chain.puts[chain.puts.strike <= short_target].sort_values("strike", ascending=False)
                if len(strikes) < 2:
                    continue
                short_opt = strikes.iloc[0]
                long_cands = chain.puts[chain.puts.strike < float(short_opt["strike"]) - 2]
                if long_cands.empty:
                    continue
                long_opt = long_cands.iloc[-1]
            elif rsi > 55 and not above_ema:
                direction = "bear_call_spread"
                dist = 0.05 if vix < 25 else 0.07
                short_target = round(price * (1 + dist))
                strikes = chain.calls[chain.calls.strike >= short_target].sort_values("strike")
                if len(strikes) < 2:
                    continue
                short_opt = strikes.iloc[0]
                long_cands = chain.calls[chain.calls.strike > float(short_opt["strike"]) + 2]
                if long_cands.empty:
                    continue
                long_opt = long_cands.iloc[0]
            else:
                continue

            short_strike = float(short_opt["strike"])
            long_strike  = float(long_opt["strike"])
            width = abs(short_strike - long_strike)
            credit = round(float(short_opt.get("lastPrice", 0)) - float(long_opt.get("lastPrice", 0)), 2)
            if credit < 0.25:
                continue
            max_loss = round((width - credit) * 100, 2)
            max_loss_pct = round(credit / width * 100, 1)

            opportunities.append({
                "symbol": sym, "price": round(price, 2),
                "strategy": direction, "expiry": target_exp,
                "short_strike": short_strike, "long_strike": long_strike,
                "credit": credit, "max_loss": max_loss,
                "max_loss_pct": round(max_loss / 20000 * 100, 2),
                "iv_rank": iv_rank, "rsi": rsi, "macd": macd,
                "above_ema200": above_ema,
                "score": round((iv_rank or 50) * 0.4 + (50 - abs(rsi - 45)) * 0.4 + credit * 10 * 0.2, 1),
            })
            time.sleep(0.15)

        except Exception as e:
            if "404" not in str(e):
                pass
            continue

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities

# ── Diagonal spread scan ──────────────────────────────────────────────
def scan_diagonals(tickers, vix):
    """
    Poor man's covered call/put — diagonal spread.
    Sell near-term option, buy further-dated same strike.
    Best when: IV rank high on near-term, stock near target strike.
    Max loss defined: net debit paid.
    No shares needed.
    """
    opportunities = []
    print(f"Scanning {len(tickers)} tickers for diagonal spreads...")

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            price = get_price(t, sym)
            if not price or price < 5:
                continue
            if get_avg_volume(t) < 5_000_000:
                continue

            exps = t.options
            if not exps or len(exps) < 2:
                continue

            # Need two expiries: near (2-6 weeks) and far (6-12 weeks)
            near_exp = far_exp = None
            for exp in exps:
                days = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now().date()).days
                if 14 <= days <= 45 and near_exp is None:
                    near_exp = exp
                elif 45 < days <= 90 and far_exp is None:
                    far_exp = exp
                if near_exp and far_exp:
                    break

            if not near_exp or not far_exp:
                continue

            earnings = get_earnings(t)
            if earnings.get("within_14d"):
                continue

            iv_rank = get_iv_rank(t)
            # Diagonals need high IV to sell premium — minimum 40
            if iv_rank is None or iv_rank < 40:
                continue

            tech = get_technicals(t, price)
            rsi  = tech.get("rsi14", 50)
            above_ema = tech.get("above_ema200", True)

            # Bullish diagonal (PMCC): stock near or just above target
            # Bearish diagonal (PMPP): stock near or just below target
            if above_ema and rsi < 65:
                # Bullish — sell near call, buy far call at same strike
                target_strike_pct = 1.02  # 2% above current price
                option_type = "call"
            elif not above_ema and rsi > 35:
                # Bearish — sell near put, buy far put at same strike
                target_strike_pct = 0.98  # 2% below current price
                option_type = "put"
            else:
                continue

            target_strike = round(price * target_strike_pct)

            near_chain = t.option_chain(near_exp)
            far_chain  = t.option_chain(far_exp)

            if option_type == "call":
                near_opts = near_chain.calls
                far_opts  = far_chain.calls
            else:
                near_opts = near_chain.puts
                far_opts  = far_chain.puts

            if near_opts.empty or far_opts.empty:
                continue

            # Find nearest available strikes
            near_row = near_opts.iloc[(near_opts.strike - target_strike).abs().argsort()[:1]]
            far_row  = far_opts.iloc[(far_opts.strike - target_strike).abs().argsort()[:1]]

            if near_row.empty or far_row.empty:
                continue

            near_strike = float(near_row["strike"].iloc[0])
            far_strike  = float(far_row["strike"].iloc[0])

            near_price  = float(near_row["lastPrice"].iloc[0])
            far_price   = float(far_row["lastPrice"].iloc[0])
            near_oi     = int(near_row["openInterest"].iloc[0]) if not near_row.empty else 0
            far_oi      = int(far_row["openInterest"].iloc[0]) if not far_row.empty else 0

            if near_oi < 50 or far_oi < 50:
                continue
            if near_price < 0.10 or far_price < 0.10:
                continue

            # Net debit = cost to enter (buy far, sell near)
            net_debit = round((far_price - near_price) * 100, 2)
            if net_debit <= 0:
                continue  # Should always be a debit, not credit

            # Max loss = net debit paid
            max_loss_pct = round(net_debit / 20000 * 100, 3)
            if max_loss_pct > 2.0:
                continue

            # Estimated max profit: if stock pins near strike at near expiry
            # Far option retains most of its value, near expires worthless
            # Rough estimate: 2-3x the net debit is realistic
            est_max_profit = round(net_debit * 2.5, 2)

            # Score: higher IV rank = better premium, closer to strike = better pin risk
            strike_distance_pct = abs(near_strike - price) / price * 100
            score = round(
                (iv_rank or 50) * 0.5 +
                max(0, 10 - strike_distance_pct) * 3 +
                (50 - abs(rsi - 50)) * 0.3,
                1
            )

            opportunities.append({
                "symbol": sym, "price": round(price, 2),
                "strategy": "diagonal_spread",
                "option_type": option_type,
                "near_expiry": near_exp, "far_expiry": far_exp,
                "strike": near_strike,
                "near_price": near_price, "far_price": far_price,
                "net_debit": net_debit,
                "max_loss": net_debit,
                "max_loss_pct": max_loss_pct,
                "est_max_profit": est_max_profit,
                "iv_rank": iv_rank, "rsi": rsi,
                "above_ema200": above_ema,
                "near_oi": near_oi, "far_oi": far_oi,
                "score": score,
            })
            time.sleep(0.15)

        except Exception as e:
            continue

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities

# ── Iron condor scan ──────────────────────────────────────────────────
def scan_iron_condors(tickers, vix):
    """Iron condors — range-bound, collect from both sides."""
    if vix < 13 or vix > 30:
        print(f"VIX {vix:.1f} outside condor range (13-30) — skipping condor scan")
        return []

    opportunities = []
    print(f"Scanning {len(tickers)} tickers for iron condors...")

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            price = get_price(t, sym)
            if not price or price < 10:
                continue
            if get_avg_volume(t) < 5_000_000:
                continue

            exps = t.options
            if not exps:
                continue

            target_exp = None
            for exp in exps:
                days = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now().date()).days
                if 7 <= days <= 21:
                    target_exp = exp
                    break
            if not target_exp:
                continue

            iv_rank = get_iv_rank(t)
            if iv_rank is None or iv_rank < 35:
                continue

            tech = get_technicals(t, price)
            rsi = tech.get("rsi14", 50)
            if rsi < 35 or rsi > 65:
                continue  # Need range-bound

            earnings = get_earnings(t)
            if earnings.get("within_14d"):
                continue

            chain = t.option_chain(target_exp)
            if chain.calls.empty or chain.puts.empty:
                continue

            dist = 0.05 if vix < 22 else 0.07
            put_short_target  = round(price * (1 - dist))
            call_short_target = round(price * (1 + dist))

            put_strikes  = chain.puts[chain.puts.strike <= put_short_target].sort_values("strike", ascending=False)
            call_strikes = chain.calls[chain.calls.strike >= call_short_target].sort_values("strike")

            if put_strikes.empty or call_strikes.empty:
                continue

            put_short  = put_strikes.iloc[0]
            call_short = call_strikes.iloc[0]

            put_long_cands  = chain.puts[chain.puts.strike < float(put_short["strike"]) - 2]
            call_long_cands = chain.calls[chain.calls.strike > float(call_short["strike"]) + 2]

            if put_long_cands.empty or call_long_cands.empty:
                continue

            put_long  = put_long_cands.iloc[-1]
            call_long = call_long_cands.iloc[0]

            total_credit = round(
                float(put_short.get("lastPrice", 0)) +
                float(call_short.get("lastPrice", 0)) -
                float(put_long.get("lastPrice", 0)) -
                float(call_long.get("lastPrice", 0)), 2
            )
            if total_credit < 0.40:
                continue

            width = abs(float(put_short["strike"]) - float(put_long["strike"]))
            max_loss_pct = round((width - total_credit) / 20000 * 100, 2)
            if max_loss_pct > 2.0:
                continue

            score = round((iv_rank or 50) * 0.4 + (50 - abs(rsi - 50)) * 0.4 + total_credit * 10 * 0.2, 1)

            opportunities.append({
                "symbol": sym, "price": round(price, 2),
                "strategy": "iron_condor", "expiry": target_exp,
                "put_short": float(put_short["strike"]),
                "put_long": float(put_long["strike"]),
                "call_short": float(call_short["strike"]),
                "call_long": float(call_long["strike"]),
                "credit": total_credit,
                "max_loss_pct": max_loss_pct,
                "iv_rank": iv_rank, "rsi": rsi,
                "score": score,
            })
            time.sleep(0.15)

        except:
            continue

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities

# ── Collar candidates ─────────────────────────────────────────────────
def scan_collar_candidates(tickers):
    """Stocks under $30 suitable for Amit's manual collar strategy."""
    candidates = []
    print("Scanning for collar candidates...")

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            price = get_price(t, sym)
            if not price or price > 30 or price < 5:
                continue
            if get_avg_volume(t) < 1_000_000:
                continue

            exps = t.options
            if not exps:
                continue

            iv_rank = get_iv_rank(t)
            if iv_rank is None or iv_rank < 40:
                continue

            earnings = get_earnings(t)
            if earnings.get("within_14d"):
                continue

            tech = get_technicals(t, price)
            score = round((iv_rank or 50) * 0.6 + (tech.get("rsi14", 50)) * 0.2, 1)

            candidates.append({
                "symbol": sym, "price": round(price, 2),
                "iv_rank": iv_rank, "rsi": tech.get("rsi14", 50),
                "score": score,
                "note": "Collar candidate — requires owning shares (Amit manual only)"
            })
            time.sleep(0.15)

        except:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:5]

# ── Main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    vix = get_vix()
    tickers = discover_tickers()
    print(f"[scanner] VIX: {vix:.1f} | Universe: {len(tickers)} tickers | Mode: {mode}")

    results = {}

    if mode in ("all", "credit_spreads", "both"):
        spreads = scan_credit_spreads(tickers, vix)
        results["credit_spreads"] = {
            "ts": datetime.now().isoformat(),
            "scan_type": "credit_spreads",
            "vix": vix,
            "tickers_scanned": len(tickers),
            "opportunities_found": len(spreads),
            "top_opportunities": spreads[:10],
        }
        out = f"{CACHE}/credit_spread_scan.json"
        with open(out, "w") as f:
            json.dump(results["credit_spreads"], f, indent=2)
        print(f"[scanner] Credit spreads: {len(spreads)} found → {out}")

    if mode in ("all", "diagonals", "both"):
        diagonals = scan_diagonals(tickers, vix)
        results["diagonals"] = {
            "ts": datetime.now().isoformat(),
            "scan_type": "diagonal_spreads",
            "vix": vix,
            "tickers_scanned": len(tickers),
            "opportunities_found": len(diagonals),
            "top_opportunities": diagonals[:10],
        }
        out = f"{CACHE}/diagonal_scan.json"
        with open(out, "w") as f:
            json.dump(results["diagonals"], f, indent=2)
        print(f"[scanner] Diagonals: {len(diagonals)} found → {out}")

    if mode in ("all", "condors", "both"):
        condors = scan_iron_condors(tickers, vix)
        results["condors"] = {
            "ts": datetime.now().isoformat(),
            "scan_type": "iron_condors",
            "vix": vix,
            "tickers_scanned": len(tickers),
            "opportunities_found": len(condors),
            "top_opportunities": condors[:10],
        }
        out = f"{CACHE}/condor_scan.json"
        with open(out, "w") as f:
            json.dump(results["condors"], f, indent=2)
        print(f"[scanner] Iron condors: {len(condors)} found → {out}")

    if mode in ("all", "collars"):
        collars = scan_collar_candidates(tickers)
        results["collars"] = {
            "ts": datetime.now().isoformat(),
            "scan_type": "collar_candidates",
            "top_candidates": collars,
        }
        out = f"{CACHE}/collar_candidates.json"
        with open(out, "w") as f:
            json.dump(results["collars"], f, indent=2)
        print(f"[scanner] Collar candidates: {len(collars)} found → {out}")

    # Summary for debate chamber to consume
    if mode in ("all", "both"):
        print("\n=== SCAN SUMMARY ===")
        for k, v in results.items():
            n = v.get("opportunities_found", len(v.get("top_candidates", v.get("top_opportunities",[]))))
            print(f"  {k}: {n} opportunities")
        if results.get("credit_spreads", {}).get("top_opportunities"):
            print("\n=== TOP CREDIT SPREADS ===")
            print(json.dumps(results["credit_spreads"]["top_opportunities"][:5], indent=2))
        if results.get("diagonals", {}).get("top_opportunities"):
            print("\n=== TOP DIAGONALS ===")
            print(json.dumps(results["diagonals"]["top_opportunities"][:5], indent=2))
        if results.get("condors", {}).get("top_opportunities"):
            print("\n=== TOP CONDORS ===")
            print(json.dumps(results["condors"]["top_opportunities"][:5], indent=2))
