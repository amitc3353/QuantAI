"""Agent Gamma — Connors RSI(10) Pullback Strategy.

Single-strategy mean-reversion agent: buys oversold pullbacks
(RSI(10) < 30) within confirmed long-term uptrends (price > 200 SMA)
via 14-21 DTE bull-call debit spreads. Exits when RSI(10) > 40.

Backtested 1996-2019 SPX: 88.89% WR, +1.17% EV/trade.
"""
from __future__ import annotations

# ── Account-level parameters ──────────────────────────────────────────────────
ACCOUNT_SIZE = 10_000               # paper starting equity (display only)
MAX_RISK_PER_TRADE = 0.01           # 1% of equity per trade
MAX_OPEN_POSITIONS = 3              # never more than 3 Gamma positions at once
MAX_DAILY_ENTRIES = 2               # max 2 new entries per day
MAX_POSITIONS_SAME_SECTOR = 2       # max 2 in same sector
CIRCUIT_BREAKER_LOSSES = 3          # pause 48h after 3 consecutive losses
CIRCUIT_BREAKER_HOURS = 48
EARNINGS_BLACKOUT_DAYS = 7          # no entry within 7 trading days of earnings
EARNINGS_POST_DAYS = 2              # no entry within 2 days *after* earnings (gap risk)
HOLD_PERIOD_MAX_DAYS = 10           # time stop: 10 trading days
RSI_ENTRY_THRESHOLD = 30            # scan signal: RSI(10) < 30
RSI_REVALIDATE_SOFT = 35            # morning re-validation soft bound (allows overnight tick-up)
RSI_EXIT_THRESHOLD = 40             # primary exit: RSI(10) > 40
TARGET_DTE = 18                     # center of 14-21 range
DTE_MIN, DTE_MAX = 14, 21
DTE_MIN_FALLBACK, DTE_MAX_FALLBACK = 10, 28

# Long leg target delta (ATM) and short leg target delta (OTM)
LONG_DELTA_TARGET = 0.50
LONG_DELTA_TOL = 0.10
SHORT_DELTA_TARGET = 0.27
SHORT_DELTA_TOL = 0.08
MIN_REWARD_RISK = 0.8               # spread must offer ≥ 0.8x reward:risk

LIQUIDITY_MIN_VOLUME = 1_000_000    # 20-day avg volume floor (stocks only)


# ── Instrument universe ───────────────────────────────────────────────────────
# 4 indices (Section 1256 tax) + 3 ETFs + 20 mega-cap stocks
INSTRUMENT_CONFIG: dict[str, dict] = {
    # Index options — Section 1256, European, cash-settled
    "XSP":   {"type": "index", "exchange": "CBOE", "sec_type": "OPT", "multiplier": 100, "tax": "1256",     "sector": "index"},
    "SPX":   {"type": "index", "exchange": "CBOE", "sec_type": "OPT", "multiplier": 100, "tax": "1256",     "sector": "index"},
    "NDX":   {"type": "index", "exchange": "CBOE", "sec_type": "OPT", "multiplier": 100, "tax": "1256",     "sector": "index"},
    "RUT":   {"type": "index", "exchange": "CBOE", "sec_type": "OPT", "multiplier": 100, "tax": "1256",     "sector": "index"},
    # ETF options — standard tax
    "SPY":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "QQQ":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "IWM":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    # Individual stocks
    "AAPL":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "MSFT":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "NVDA":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "GOOGL": {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "AMZN":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "META":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "TSLA":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "BRK.B": {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "JPM":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "V":     {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "UNH":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "MA":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "HD":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "PG":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "JNJ":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "XOM":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "CVX":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "COST":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "AVGO":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "LLY":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
}

UNIVERSE = list(INSTRUMENT_CONFIG.keys())  # 27 symbols


def yf_symbol(symbol: str) -> str:
    """Translate our config symbol into the symbol yfinance expects.

    BRK.B → BRK-B for Yahoo. Indices use Yahoo's ^-prefixed tickers.
    XSP → SPY: SPY is ~1/10 SPX (same scale as XSP), so the close price and
    SMA(200) match XSP-strike chains. RSI is dimensionless. We keep XSP as
    the broker routing target for Section 1256 tax treatment.
    """
    if symbol == "BRK.B":
        return "BRK-B"
    if symbol == "SPX":
        return "^GSPC"
    if symbol == "NDX":
        return "^NDX"
    if symbol == "RUT":
        return "^RUT"
    if symbol == "XSP":
        return "SPY"
    return symbol


# Map from our config symbols to the strings IBKR expects when qualifying
# the underlying. Most symbols pass through unchanged; a handful need a
# format swap (e.g. BRK.B → BRK B for the IBKR localSymbol convention).
IBKR_SYMBOL_MAP: dict[str, str] = {
    "BRK.B": "BRK B",
}


def ibkr_symbol(symbol: str) -> str:
    return IBKR_SYMBOL_MAP.get(symbol, symbol)
