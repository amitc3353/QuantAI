"""Universe membership and config sanity tests (added 2026-05-09 expansion)."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma import UNIVERSE, INSTRUMENT_CONFIG  # noqa: E402


# ── Universe shape ──────────────────────────────────────────────────────


def test_universe_count():
    """Pre-fix: 27. Post-fix (2026-05-09): 155."""
    assert len(UNIVERSE) == 155


def test_no_duplicates():
    assert len(set(UNIVERSE)) == len(UNIVERSE)


def test_universe_derives_from_config():
    """UNIVERSE is `list(INSTRUMENT_CONFIG.keys())` — one source of truth."""
    assert UNIVERSE == list(INSTRUMENT_CONFIG.keys())


# ── Config schema ───────────────────────────────────────────────────────


def test_all_symbols_have_full_config():
    REQUIRED_KEYS = {"type", "exchange", "sec_type", "multiplier", "tax", "sector"}
    for sym, cfg in INSTRUMENT_CONFIG.items():
        missing = REQUIRED_KEYS - set(cfg.keys())
        assert not missing, f"{sym} missing keys: {missing}"
        assert cfg["multiplier"] == 100, f"{sym} multiplier={cfg['multiplier']}"
        assert cfg["sec_type"] == "OPT", f"{sym} sec_type={cfg['sec_type']}"


def test_sector_normalized():
    """Every sector value must be in the canonical taxonomy."""
    ALLOWED_SECTORS = {
        "Technology", "Financials", "Healthcare", "Industrials",
        "ConsumerDisc", "ConsumerStaples", "Communications",
        "Energy", "Utilities", "RealEstate", "Materials",
        "etf", "index",
    }
    for sym, cfg in INSTRUMENT_CONFIG.items():
        assert cfg["sector"] in ALLOWED_SECTORS, \
            f"{sym} has rogue sector {cfg['sector']!r} (allowed: {ALLOWED_SECTORS})"


def test_index_is_1256_tax():
    for sym, cfg in INSTRUMENT_CONFIG.items():
        if cfg["type"] == "index":
            assert cfg["tax"] == "1256", f"{sym} index must be tax=1256 (Section 1256)"


def test_stocks_etfs_are_standard_tax():
    for sym, cfg in INSTRUMENT_CONFIG.items():
        if cfg["type"] in ("stock", "etf"):
            assert cfg["tax"] == "standard", f"{sym} {cfg['type']} must be tax=standard"


def test_no_leveraged_etfs():
    """RSI doesn't behave the same on 3× products. Regression guard."""
    LEVERAGED = {"TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
                 "TNA", "TZA", "UPRO", "SPXU", "TMF", "TMV", "FAS", "FAZ",
                 "QLD", "QID", "SSO", "SDS", "DDM", "DXD", "UCO", "SCO"}
    intersect = set(UNIVERSE) & LEVERAGED
    assert not intersect, f"Leveraged ETFs in universe: {intersect}"


# ── Sector distribution sanity ──────────────────────────────────────────


def test_no_single_sector_dominates():
    """No sector should exceed 35% of universe — sanity, not a hard rule."""
    counts = Counter(cfg["sector"] for cfg in INSTRUMENT_CONFIG.values())
    largest_sector, largest_count = counts.most_common(1)[0]
    pct = largest_count / len(UNIVERSE)
    assert pct <= 0.35, \
        f"Sector {largest_sector} is {largest_count}/{len(UNIVERSE)} = {pct:.0%} (>35%)"


def test_all_155_symbols_classified():
    """Correction #1 from review: every one of the 155 symbols must be
    classified — no rounding errors, no missing buckets."""
    counts = Counter(cfg["sector"] for cfg in INSTRUMENT_CONFIG.values())
    total_classified = sum(counts.values())
    assert total_classified == 155, \
        f"Sector counts sum to {total_classified}, expected exactly 155"
    assert total_classified == len(UNIVERSE)


# ── Backward compatibility ──────────────────────────────────────────────


def test_existing_27_preserved():
    """Original 27 symbols must remain in UNIVERSE — no silent dropping."""
    EXISTING_27 = ["XSP", "SPX", "NDX", "RUT", "SPY", "QQQ", "IWM",
                   "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
                   "BRK.B", "JPM", "V", "UNH", "MA", "HD", "PG", "JNJ",
                   "XOM", "CVX", "COST", "AVGO", "LLY"]
    universe_set = set(UNIVERSE)
    missing = [s for s in EXISTING_27 if s not in universe_set]
    assert not missing, f"Original 27 symbols dropped from universe: {missing}"


def test_existing_27_config_unchanged():
    """The original 27 entries must keep their existing classification.
    Tests that expansion didn't accidentally re-sector existing symbols."""
    EXPECTED = {
        "XSP": ("index", "CBOE", "1256", "index"),
        "SPX": ("index", "CBOE", "1256", "index"),
        "NDX": ("index", "CBOE", "1256", "index"),
        "RUT": ("index", "CBOE", "1256", "index"),
        "SPY": ("etf", "SMART", "standard", "etf"),
        "QQQ": ("etf", "SMART", "standard", "etf"),
        "IWM": ("etf", "SMART", "standard", "etf"),
        "AAPL": ("stock", "SMART", "standard", "Technology"),
        "MSFT": ("stock", "SMART", "standard", "Technology"),
        "NVDA": ("stock", "SMART", "standard", "Technology"),
        "GOOGL": ("stock", "SMART", "standard", "Technology"),
        "AMZN": ("stock", "SMART", "standard", "ConsumerDisc"),
        "META": ("stock", "SMART", "standard", "Technology"),
        "TSLA": ("stock", "SMART", "standard", "ConsumerDisc"),
        "BRK.B": ("stock", "SMART", "standard", "Financials"),
        "JPM": ("stock", "SMART", "standard", "Financials"),
        "V": ("stock", "SMART", "standard", "Financials"),
        "UNH": ("stock", "SMART", "standard", "Healthcare"),
        "MA": ("stock", "SMART", "standard", "Financials"),
        "HD": ("stock", "SMART", "standard", "ConsumerDisc"),
        "PG": ("stock", "SMART", "standard", "ConsumerStaples"),
        "JNJ": ("stock", "SMART", "standard", "Healthcare"),
        "XOM": ("stock", "SMART", "standard", "Energy"),
        "CVX": ("stock", "SMART", "standard", "Energy"),
        "COST": ("stock", "SMART", "standard", "ConsumerStaples"),
        "AVGO": ("stock", "SMART", "standard", "Technology"),
        "LLY": ("stock", "SMART", "standard", "Healthcare"),
    }
    for sym, (typ, exch, tax, sec) in EXPECTED.items():
        cfg = INSTRUMENT_CONFIG[sym]
        assert cfg["type"] == typ, f"{sym}: type changed from {typ} to {cfg['type']}"
        assert cfg["exchange"] == exch, f"{sym}: exchange changed"
        assert cfg["tax"] == tax, f"{sym}: tax changed"
        assert cfg["sector"] == sec, f"{sym}: sector changed from {sec} to {cfg['sector']}"


def test_etf_sector_overrides_applied():
    """Sector ETFs (XLK, XLF, etc.) inherit their sector — not "etf" — so
    sector-cap-of-2 prevents over-concentration with individual stocks."""
    EXPECTED_ETF_SECTORS = {
        "XLK": "Technology",
        "XLF": "Financials",
        "XLE": "Energy",
        "XLV": "Healthcare",
        "XLI": "Industrials",
        "XLY": "ConsumerDisc",
        "XLP": "ConsumerStaples",
        "XLU": "Utilities",
        "XLB": "Materials",
        "XLC": "Communications",
        "XLRE": "RealEstate",
        "SMH": "Technology",
        "KRE": "Financials",
        "GDX": "Materials",
        "XBI": "Healthcare",
        "IBB": "Healthcare",
        "ITA": "Industrials",
        "XOP": "Energy",
        "XHB": "ConsumerDisc",
    }
    for etf, expected_sector in EXPECTED_ETF_SECTORS.items():
        assert etf in INSTRUMENT_CONFIG, f"{etf} missing from INSTRUMENT_CONFIG"
        actual = INSTRUMENT_CONFIG[etf]["sector"]
        assert actual == expected_sector, \
            f"{etf} sector={actual!r}, expected {expected_sector!r}"


def test_broad_market_etfs_stay_etf():
    """Broad-market ETFs that don't track a specific sector stay as 'etf'."""
    BROAD_ETFS = {"DIA", "MDY", "IJR", "EFA", "EEM", "TLT", "GLD", "SLV",
                  "SPY", "QQQ", "IWM"}
    for etf in BROAD_ETFS:
        if etf in INSTRUMENT_CONFIG:
            assert INSTRUMENT_CONFIG[etf]["sector"] == "etf", \
                f"{etf} should be sector=etf, got {INSTRUMENT_CONFIG[etf]['sector']!r}"
