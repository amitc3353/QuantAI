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
# Original (2026-04): 4 indices + 3 ETFs + 20 mega-cap stocks = 27 symbols.
# Expansion (2026-05-09): added 101 S&P 100 stocks + 27 sector/thematic/broad
# ETFs = 155 total. See docs/gamma-universe-expansion-implementation-plan.md
# for the expansion rationale and yahoo→QA sector mapping table.
#
# Sector taxonomy (13 values):
#   Existing:  Technology, Financials, Healthcare, ConsumerDisc, ConsumerStaples,
#              Energy, etf, index
#   Added:     Communications, Industrials, RealEstate, Utilities, Materials
#
# Sector ETFs (XLK, XLF, ...) inherit their tracked sector (e.g. XLK→Technology)
# so the sector-cap-of-2 rule prevents over-concentration when both XLK and AAPL
# qualify. Broad-market ETFs (DIA, MDY, EFA, etc.) stay sector="etf".
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
    # ── EXPANSION 2026-05-09 ──────────────────────────────────────────────
    # 101 S&P 100 stocks (BLK + EQIX dropped: vol <1M; MMC dropped: ticker change)
    "ABBV":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "ABT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "ACN":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "ADBE":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "AEP":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "AFL":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "AIG":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "AMAT":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "AMD":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "AMGN":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "AMT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "RealEstate"},
    "APD":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Materials"},
    "AXP":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "BA":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "BAC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "BK":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "BKNG":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "BMY":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "C":     {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "CAT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "CCI":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "RealEstate"},
    "CI":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "CL":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "CMCSA": {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "COF":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "COP":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "CRM":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "CSCO":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "CVS":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "DE":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "DHR":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "DIS":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "DUK":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "ELV":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "EMR":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "EOG":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "EXC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "F":     {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "FCX":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Materials"},
    "GD":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "GE":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "GILD":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "GM":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "GOOG":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "GS":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "HON":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "IBM":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "INTC":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "INTU":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "ISRG":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "KHC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "KO":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "LIN":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Materials"},
    "LMT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "LOW":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "MCD":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "MDLZ": {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "MDT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "MET":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "MMM":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "MO":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "MRK":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "MS":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "MU":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "NEE":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "NFLX":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "NKE":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "NOW":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "ORCL":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "ORLY":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "PEP":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "PFE":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "PLD":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "RealEstate"},
    "PM":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "PNC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "PRU":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "PSX":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "PYPL":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "QCOM":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "RTX":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "SBUX":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "SCHW":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "SLB":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "SO":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "SPG":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "RealEstate"},
    "SPGI":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "STZ":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "T":     {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "TFC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "TGT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "TJX":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "TMO":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "TMUS":  {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "TRV":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "TXN":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "UNP":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "UPS":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "USB":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "VZ":    {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "WFC":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "WMT":   {"type": "stock", "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    # 27 sector + thematic + broad-market ETFs
    "DIA":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "EEM":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "EFA":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "GDX":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Materials"},
    "GLD":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "IBB":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "IJR":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "ITA":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "KRE":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "MDY":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "SLV":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "SMH":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "TLT":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "etf"},
    "XBI":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "XHB":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "XLB":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Materials"},
    "XLC":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Communications"},
    "XLE":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
    "XLF":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Financials"},
    "XLI":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Industrials"},
    "XLK":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Technology"},
    "XLP":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerStaples"},
    "XLRE":  {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "RealEstate"},
    "XLU":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Utilities"},
    "XLV":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Healthcare"},
    "XLY":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "ConsumerDisc"},
    "XOP":   {"type": "etf",   "exchange": "SMART","sec_type": "OPT", "multiplier": 100, "tax": "standard", "sector": "Energy"},
}

UNIVERSE = list(INSTRUMENT_CONFIG.keys())  # 155 symbols (27 existing + 128 added 2026-05-09)


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
