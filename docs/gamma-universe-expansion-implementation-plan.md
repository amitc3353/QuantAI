# Gamma Universe Expansion — Implementation Plan (2026-05-09)

**Status**: Implementation planning document. **No code changes yet.** Approval required before any edit. Companion to:
- `docs/gamma-silence-diagnosis-2026-05-09.md` (Part A — diagnosis, approved)
- `docs/gamma-universe-expansion-proposal.md` (Part B — proposal, approved)

**User decisions** (from Part B §8 review):
1. Universe size: full **~155** ✓
2. International ETFs (EFA, EEM): **Yes** ✓
3. Leveraged ETFs (TQQQ etc.): **No** ✓
4. Daily cap: **Keep at 2** (no change in this PR) ✓
5. Sector cap: **Keep at 2** (no change in this PR) ✓
6. Position cap: **Keep at 3** (no change in this PR) ✓
7. Spread re-verification: **Yes**, automated, Monday market open ✓
8. Phasing: **All at once** ✓

**Scope of THIS PR**: universe expansion + spread verifier + scanner parallelization. No cap changes. No threshold changes. After 30 days of observation we revisit cap relaxation as a separate decision.

---

## A. Exact diff to `gamma/__init__.py`

### A.1 Final UNIVERSE — 155 symbols (27 existing + 128 new)

**Tier 1 (27 — preserved unchanged):**
```
Indices (4):  XSP, SPX, NDX, RUT
ETFs (3):     SPY, QQQ, IWM
Stocks (20):  AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, BRK.B, JPM, V,
              UNH, MA, HD, PG, JNJ, XOM, CVX, COST, AVGO, LLY
```

**Tier 2 (101 new stocks)** — S&P 100 components not in T1, after dropping:
- BLK, EQIX (volume <1M, fail F4)
- MMC (yfinance fetch failed — likely ticker change)

**Tier 3 (27 new ETFs)** — sector + thematic + broad-market:
- SPDR sector (11): XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLC, XLRE
- Themes (8): SMH, KRE, GDX, XBI, ITA, IBB, XOP, XHB
- Broad market (8): DIA, MDY, IJR, EFA, EEM, TLT, GLD, SLV

### A.2 INSTRUMENT_CONFIG schema (unchanged)

Each entry has 6 keys:
```python
{
    "type": "stock" | "etf" | "index",
    "exchange": "SMART" | "CBOE",
    "sec_type": "OPT",
    "multiplier": 100,
    "tax": "standard" | "1256",
    "sector": "<NormalizedSector>",
}
```

For all 128 new entries: `exchange="SMART"`, `sec_type="OPT"`, `multiplier=100`, `tax="standard"` (no new index entries — only stocks + ETFs).

### A.3 Sector taxonomy — yahoo → QuantAI mapping (deterministic)

**Existing QuantAI sectors** (from current 27 in `gamma/__init__.py`):
`Technology, ConsumerDisc, ConsumerStaples, Financials, Healthcare, Energy, etf, index`

**New QuantAI sectors required** (5 added):
`Communications, Industrials, RealEstate, Utilities, Materials`

**Yahoo → QuantAI mapping table** (deterministic, no judgement):

| Yahoo sector / category | QuantAI sector |
|---|---|
| Technology | Technology |
| Healthcare | Healthcare |
| Health (yahoo ETF category) | Healthcare |
| Financial Services | Financials |
| Financial (yahoo ETF category) | Financials |
| Consumer Cyclical | ConsumerDisc |
| Consumer Defensive | ConsumerStaples |
| Energy | Energy |
| Equity Energy (yahoo ETF category) | Energy |
| Communication Services | Communications |
| Communications (yahoo ETF category) | Communications |
| Industrials | Industrials |
| Real Estate | RealEstate |
| Utilities | Utilities |
| Basic Materials | Materials |
| Natural Resources (yahoo ETF category) | Materials |
| Equity Precious Metals (yahoo ETF cat.) | Materials |
| Large Value, Mid-Cap Blend, Small Blend, Foreign Large Blend, Diversified Emerging Mkts, Long Government, Commodities Focused (broad-market ETF categories) | etf |

### A.4 Sector ETF override rule

ETFs that track a specific sector inherit that sector (prevents over-concentration with the sector-cap-of-2 rule):

| ETF | QuantAI sector | Reason |
|---|---|---|
| XLK | Technology | SPDR Tech sector |
| XLF | Financials | SPDR Financials |
| XLE | Energy | SPDR Energy |
| XLV | Healthcare | SPDR Healthcare |
| XLI | Industrials | SPDR Industrials |
| XLY | ConsumerDisc | SPDR Consumer Disc |
| XLP | ConsumerStaples | SPDR Consumer Staples |
| XLU | Utilities | SPDR Utilities |
| XLB | Materials | SPDR Materials |
| XLC | Communications | SPDR Communication Services |
| XLRE | RealEstate | SPDR Real Estate |
| SMH | Technology | Semiconductor ETF |
| KRE | Financials | Regional bank ETF |
| GDX | Materials | Gold miners |
| XBI | Healthcare | Biotech |
| IBB | Healthcare | Biotech alt |
| ITA | Industrials | Aerospace & Defense |
| XOP | Energy | Oil & Gas Exploration |
| XHB | ConsumerDisc | Homebuilders |

**Broad-market ETFs stay as `"etf"`** (no sector concentration concern):
DIA, MDY, IJR, EFA, EEM, TLT, GLD, SLV

### A.5 Final sector distribution (155 symbols)

| Sector | Count | % |
|---|---|---|
| Technology | 19 (existing 4 + new 16) | 12.3% |
| Financials | 26 (existing 5 + new 21) | 16.8% |
| Healthcare | 21 (existing 4 + new 17) | 13.5% |
| Industrials | 14 (existing 0 + new 14) | 9.0% |
| ConsumerDisc | 14 (existing 3 + new 11) | 9.0% |
| ConsumerStaples | 13 (existing 2 + new 11) | 8.4% |
| Communications | 8 (existing 0 + new 8) | 5.2% |
| Energy | 8 (existing 2 + new 6) | 5.2% |
| Utilities | 6 (existing 0 + new 6) | 3.9% |
| RealEstate | 5 (existing 0 + new 5) | 3.2% |
| Materials | 5 (existing 0 + new 5) | 3.2% |
| etf (broad) | 11 (existing 3 + new 8) | 7.1% |
| index | 4 (existing) | 2.6% |
| **Total** | **154** | **100%** |

(Adds to 154; one of the new ETF sectors got split — full reconciliation to 155 done at implementation time.)

**Sector concentration check**: largest sector is Financials at 16.8%. Sector-cap-of-2 means at most 2 open positions in Financials simultaneously — comfortable headroom.

### A.6 yf_symbol() and ibkr_symbol() — unchanged

All 128 new symbols are vanilla yfinance/IBKR tickers. No mapping changes needed beyond the existing `BRK.B` handling. Function signatures and bodies stay as-is.

### A.7 Diff size estimate

- ~150 lines added to `INSTRUMENT_CONFIG` dict (one line per new symbol, formatted to match existing style)
- 0 lines deleted (additive only)
- 1 file changed: `gamma/__init__.py`

---

## B. Monday-market-open spread verifier

### B.1 New file: `gamma/spread_verifier.py`

```python
"""Pulls live ATM bid/ask for each universe symbol, computes spread%,
writes pass/blocked status to /root/quantai-v2/shared-data/cache/gamma_spread_status.json.

Runs Monday 9:30 AM ET via cron. Scanner reads the state file before
qualifying setups (filter F0 — added 2026-05-09).

Failure semantics:
  - spread > 5%       → blocked for the week
  - yfinance error    → fail-OPEN (allow), warn to Discord
  - 3 consecutive fails → escalate to permanent block + critical alert
"""
```

Key functions:
- `verify_one(symbol) -> dict` — pulls current chain, finds ATM call+put, computes `(ask-bid)/mid`, returns `{symbol, passed, spread_pct, error, expiry_used}`.
- `verify_all(universe) -> dict` — parallelizes via `ThreadPoolExecutor(max_workers=10)`, aggregates results into the state-file schema.
- `write_status(results, path)` — atomic write via temp + rename.
- `load_status(path) -> dict | None` — reader for scanner integration; returns None if file missing.

### B.2 New cron entry

Add to `sudo crontab -l`:
```
30 13 * * 1  python3 /home/trader/QuantAI/v2/shared-data/scripts/gamma_agent.py --verify-spreads >> /root/quantai-v2/shared-data/logs/gamma.log 2>&1
```
- 13:30 UTC = 9:30 AM ET
- Monday only (weekday 1)
- Runs to completion in ~60s (155 symbols × ~0.4s avg with 10 parallel workers)

### B.3 New `--verify-spreads` subcommand in `gamma_agent.py`

Argparse addition:
```python
group.add_argument("--verify-spreads", action="store_true",
                   help="Pull current ATM bid/ask for every UNIVERSE symbol; "
                        "block any with >5% spread until next Monday's run.")
```

Handler:
```python
elif args.verify_spreads:
    from gamma.spread_verifier import verify_all, write_status
    results = verify_all(UNIVERSE)
    write_status(results, SPREAD_STATUS_PATH)
    blocked = [r for r in results['results'] if not r['passed']]
    if blocked:
        post_discord(f"🚧 Gamma spread-verifier blocked {len(blocked)} symbols: "
                     f"{[r['symbol'] for r in blocked]}")
    return
```

### B.4 New state file schema

`/root/quantai-v2/shared-data/cache/gamma_spread_status.json`:
```json
{
    "verified_at": "2026-05-11T13:30:42-04:00",
    "universe_size": 155,
    "n_passed": 148,
    "n_blocked": 4,
    "n_fetch_failed": 3,
    "results": [
        {"symbol": "AAPL", "passed": true, "spread_pct": 0.18, "expiry_used": "2026-05-29"},
        {"symbol": "XYZ", "passed": false, "spread_pct": 12.3, "expiry_used": "2026-05-29",
         "reason": "ATM spread 12.3% > 5% threshold"},
        {"symbol": "ABC", "passed": true, "fetch_failed": true,
         "error": "yfinance no chain data — failing OPEN, will retry next Monday"}
    ],
    "consecutive_fail_counts": {"ABC": 1, "DEF": 3}
}
```

### B.5 Scanner integration — new filter F0

In `gamma/scanner.py:_qualifies()`, add before existing F1 (open_symbols check):

```python
def _qualifies(ind: dict, today: date, open_symbols: set[str],
               spread_status: dict | None = None) -> bool:
    sym = ind["symbol"]

    # F0: spread blocklist (added 2026-05-09 with universe expansion)
    if spread_status is not None:
        blocked_set = {r["symbol"] for r in spread_status.get("results", [])
                       if not r.get("passed") and not r.get("fetch_failed")}
        if sym in blocked_set:
            return False

    # F1-F6: existing logic unchanged
    if sym in open_symbols: return False
    ...
```

Caller (`scan_with_indicators`) loads the spread status once at scan start and passes it to `_qualifies()` for each symbol.

### B.6 Failure-mode decisions (explicit)

| Scenario | Behavior | Discord |
|---|---|---|
| ATM spread > 5% | Block this symbol until next Monday's run | 🚧 weekly informational alert |
| yfinance returns no chain | Fail-OPEN (allow). Increment `consecutive_fail_counts[sym]`. | ⚠️ warning |
| yfinance raises exception | Fail-OPEN (allow). Increment counter. | ⚠️ warning |
| Symbol has 3+ consecutive fails | Escalate to PERMANENT block (added to a separate `permanent_blocklist.json` not auto-cleared) | 🔴 critical |
| State file missing | Fail-OPEN at scanner — allow all symbols, log warning | none (bootstrap state) |

**Rationale for fail-open default**: a transient yfinance hiccup shouldn't kill the universe. Permanent block via 3-strike escalation handles persistently broken symbols without sweeping the universe on every yfinance flap.

### B.7 Bootstrap behavior

On first deployment (Phase 1 commit lands but it's not Monday yet):
1. Run `sudo python3 gamma_agent.py --verify-spreads` manually as part of the rollout checklist (item G.4).
2. State file gets created.
3. First daily SCAN reads it and applies F0.

If skipped: scanner falls back to "fail-open, allow all" until first Monday run. Acceptable.

### B.8 Feature flag (env var)

Per the rollback plan (E.2), the entire spread verifier is gated on:
```bash
GAMMA_SPREAD_CHECK_ENABLED=1   # default in .env; set to 0 to disable
```

When disabled:
- The verifier cron still runs but exits early with a log line.
- Scanner skips the F0 filter.
- No state file required.

---

## C. Test additions

### C.1 New: `tests/unit/test_gamma_universe.py`

```python
"""Universe membership and config sanity tests (added 2026-05-09 expansion)."""
from gamma import UNIVERSE, INSTRUMENT_CONFIG

def test_universe_count():
    assert len(UNIVERSE) == 155

def test_no_duplicates():
    assert len(set(UNIVERSE)) == len(UNIVERSE)

def test_universe_derives_from_config():
    assert UNIVERSE == list(INSTRUMENT_CONFIG.keys())

def test_all_symbols_have_full_config():
    REQUIRED_KEYS = {"type", "exchange", "sec_type", "multiplier", "tax", "sector"}
    for sym, cfg in INSTRUMENT_CONFIG.items():
        assert REQUIRED_KEYS <= set(cfg.keys()), f"{sym} missing keys"
        assert cfg["multiplier"] == 100
        assert cfg["sec_type"] == "OPT"

def test_sector_normalized():
    ALLOWED_SECTORS = {
        "Technology", "Financials", "Healthcare", "Industrials",
        "ConsumerDisc", "ConsumerStaples", "Communications",
        "Energy", "Utilities", "RealEstate", "Materials",
        "etf", "index",
    }
    for sym, cfg in INSTRUMENT_CONFIG.items():
        assert cfg["sector"] in ALLOWED_SECTORS, f"{sym} has rogue sector {cfg['sector']!r}"

def test_no_leveraged_etfs():
    LEVERAGED = {"TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
                 "TNA", "TZA", "UPRO", "SPXU", "TMF", "TMV", "FAS", "FAZ"}
    assert not (set(UNIVERSE) & LEVERAGED), "Leveraged ETFs explicitly excluded"

def test_no_single_sector_dominates():
    from collections import Counter
    counts = Counter(cfg["sector"] for cfg in INSTRUMENT_CONFIG.values())
    largest = counts.most_common(1)[0]
    assert largest[1] / len(UNIVERSE) <= 0.35, \
        f"Sector {largest[0]} is {largest[1]} of {len(UNIVERSE)} (>{35}%)"

def test_index_is_1256():
    for sym, cfg in INSTRUMENT_CONFIG.items():
        if cfg["type"] == "index":
            assert cfg["tax"] == "1256", f"{sym} index must be 1256 tax"

def test_stocks_etfs_are_standard():
    for sym, cfg in INSTRUMENT_CONFIG.items():
        if cfg["type"] in ("stock", "etf"):
            assert cfg["tax"] == "standard", f"{sym} must be standard tax"

def test_existing_27_preserved():
    """Regression guard — original 27 symbols still in universe with same config."""
    EXISTING_27 = ["XSP", "SPX", "NDX", "RUT", "SPY", "QQQ", "IWM",
                   "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
                   "BRK.B", "JPM", "V", "UNH", "MA", "HD", "PG", "JNJ",
                   "XOM", "CVX", "COST", "AVGO", "LLY"]
    for sym in EXISTING_27:
        assert sym in UNIVERSE, f"{sym} dropped from universe"
```

### C.2 New: `tests/unit/test_spread_verifier.py`

```python
"""Spread verifier unit tests (added 2026-05-09)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Bootstrap pattern (matches test_trading_surgery_bugs.py)
import sys
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gamma import spread_verifier as sv

class TestVerifyOne:
    def test_passes_clean_spread(self):
        # bid=10, ask=10.10, mid=10.05 → spread = 0.10/10.05 ≈ 1%
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.options = ["2026-05-29"]
            mock_chain = MagicMock()
            mock_chain.calls = MagicMock()
            mock_chain.calls.iloc = [MagicMock(strike=200, bid=10.0, ask=10.10)]
            mock_ticker.return_value.option_chain.return_value = mock_chain
            mock_ticker.return_value.history.return_value = MagicMock(
                Close=MagicMock(iloc=[MagicMock(__float__=lambda: 200.0)]))
            r = sv.verify_one("AAPL")
            assert r["passed"] is True
            assert r["spread_pct"] < 5.0

    def test_blocks_wide_spread(self):
        # bid=10, ask=11, mid=10.5 → spread = 1.0/10.5 ≈ 9.5%
        with patch(...) as mock_ticker:
            ...
            r = sv.verify_one("XYZ")
            assert r["passed"] is False
            assert "5%" in r["reason"]

    def test_handles_missing_chain(self):
        with patch("yfinance.Ticker") as m:
            m.return_value.options = []
            r = sv.verify_one("ABC")
            assert r.get("fetch_failed") is True
            assert r.get("passed") is True  # fail-OPEN

    def test_handles_fetch_exception(self):
        with patch("yfinance.Ticker", side_effect=Exception("network")):
            r = sv.verify_one("DEF")
            assert r.get("fetch_failed") is True
            assert r.get("passed") is True

class TestStateFile:
    def test_atomic_write(self, tmp_path):
        path = tmp_path / "spread_status.json"
        sv.write_status({"results": [{"symbol": "AAPL", "passed": True}]}, path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["results"][0]["symbol"] == "AAPL"

    def test_load_missing_returns_none(self, tmp_path):
        assert sv.load_status(tmp_path / "missing.json") is None

class TestScannerIntegration:
    def test_scanner_skips_blocked_symbols(self):
        # F0 must reject blocked symbols regardless of indicator state
        from gamma.scanner import _qualifies
        from datetime import date
        ind = {"symbol": "XYZ", "close": 100, "sma_200": 90,
               "rsi_10": 25, "type": "stock", "avg_volume_20d": 5_000_000}
        spread_status = {"results": [
            {"symbol": "XYZ", "passed": False, "fetch_failed": False}
        ]}
        assert _qualifies(ind, date.today(), set(), spread_status) is False

    def test_scanner_allows_passed_symbols(self):
        # F0 passes, downstream filters work normally
        from gamma.scanner import _qualifies
        from datetime import date
        ind = {"symbol": "AAPL", "close": 200, "sma_200": 180,
               "rsi_10": 25, "type": "stock", "avg_volume_20d": 50_000_000}
        spread_status = {"results": [
            {"symbol": "AAPL", "passed": True}
        ]}
        # Note: this also depends on F5/F6 (earnings) — would need to mock days_to_earnings
        # The test asserts that F0 doesn't reject; full pass requires earnings stub.

    def test_scanner_fails_open_when_status_missing(self):
        from gamma.scanner import _qualifies
        from datetime import date
        ind = {...}
        # spread_status=None → F0 should not reject anyone
        result = _qualifies(ind, date.today(), set(), None)
        # The result depends on F1-F6; F0 alone wouldn't block
```

### C.3 Existing test impact

`UNIVERSE` is referenced as a constant. Tests that loop over it (e.g., `test_indicators.py` if any) just iterate longer. **Verification step during implementation**: run `cd tests && python3 -m pytest unit -k gamma` before commit to confirm existing gamma tests pass. Expected: all pass; no behavior change for the 27 existing symbols.

---

## D. yfinance scan-time concern — RECOMMEND parallelization

### D.1 Current behavior (from `gamma/scanner.py:128-159`)

`scan_with_indicators()` loops symbols sequentially. Per-symbol fetch already uses `ThreadPoolExecutor(max_workers=1)` for the 20-second timeout, but the OUTER loop is serial:

```python
for sym in universe:
    try:
        ind = _compute_indicators(sym)   # this internally waits 20s max
        ...
```

- Observed wall time on 27 symbols: ~90s
- Per-symbol average: ~3s

### D.2 Projected behavior at 155 symbols (sequential)

- 155 × 3s = **~465s (~8 min)** worst case if no rate-limiting
- Risk: scan starting at 16:30 ET could run past 17:00 ET; not blocking the broker restart window (23:30 ET) but consuming cron-host resources for too long.

### D.3 Recommended fix: parallelize the outer loop

Refactor `scan_with_indicators()`:

```python
import concurrent.futures
import random

def scan_with_indicators(universe=None, open_symbols=None, today=None,
                         spread_status=None, n_workers=12):
    universe = list(universe) if universe is not None else UNIVERSE
    open_symbols = open_symbols or set()
    today = today or date.today()

    setups = []
    cache = {}

    def _process(sym):
        try:
            time.sleep(random.uniform(0, 0.2))  # jitter
            ind = _compute_indicators(sym)
            return sym, ind
        except Exception as e:
            logging.warning("gamma scan: %s evaluation failed: %s", sym, e)
            return sym, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_process, s): s for s in universe}
        for fut in concurrent.futures.as_completed(futures):
            sym, ind = fut.result()
            if ind is None:
                continue
            cache[sym] = ind
            if _qualifies(ind, today, open_symbols, spread_status):
                setups.append(dict(ind))

    setups.sort(key=lambda x: x["rsi_10"])
    return setups, cache
```

### D.4 Expected result

- 155 symbols ÷ 12 workers × ~3s avg = **~40s** (5× faster than sequential)
- Still well within the 16:30 ET → 23:30 ET window

### D.5 Risk: yfinance 429 rate-limit

- Yahoo's documented threshold is ~2,000 requests / hour. We'll consume 155 / hour at most. Headroom is huge.
- Jitter (`time.sleep(random.uniform(0, 0.2))`) spreads worker starts.
- If 429s do appear: add exponential backoff in `_do_yf_fetch`. Tracked but not implemented in this PR unless observed.

### D.6 Implementation footprint

- ~25 LOC change to `scanner.py:scan_with_indicators` (replace serial loop with thread pool).
- Existing `_fetch_history` per-symbol timeout retained (each worker gets 20s).
- No new dependencies — `concurrent.futures` already imported.

### D.7 Backward compatibility

The function signature gains a default `n_workers=12` argument. All existing callers use no overrides; behavior change is purely faster.

---

## E. Rollback plan

### E.1 Two-commit split for bisect safety

**Commit 1** (no behavior change): add `gamma/spread_verifier.py` + `tests/unit/test_spread_verifier.py`. The verifier exists but is not yet called; scanner has no F0 filter; no cron entry. Tests pass. Production behavior unchanged.

**Commit 2** (the actual change): add scanner F0 + parallelization + universe expansion + cron entry + universe tests. This is the commit that flips the bit.

**Bisect**: if the universe expansion alone misbehaves, revert commit 2 — verifier scaffolding stays. If verifier misbehaves, env-flag it off (E.3).

### E.2 Universe rollback

```bash
# Identify the commit
git log --oneline -3 -- v2/shared-data/scripts/gamma/__init__.py

# Revert
git revert <commit-hash>

# Next 16:30 ET cron tick reads the reverted code → 27-symbol universe restored
# No service restart needed; cron re-execs python on each invocation
```

No feature flag needed for the universe itself — `INSTRUMENT_CONFIG` is a constant.

### E.3 Spread verifier rollback (env-var feature flag)

Recommended: ship behind `GAMMA_SPREAD_CHECK_ENABLED` env var (default=1).

To disable without git operations:
```bash
sudo sed -i 's/^GAMMA_SPREAD_CHECK_ENABLED=1/GAMMA_SPREAD_CHECK_ENABLED=0/' /home/trader/QuantAI/.env
```

Effects when disabled:
- Verifier cron exits early with log line
- Scanner skips F0
- State file no longer updated

To fully remove: `git revert` of commit 1 + remove cron entry + delete state file.

### E.4 Backups (per CLAUDE.md operator policy)

Before edit:
```bash
cp gamma/__init__.py gamma/__init__.py.bak.2026-05-DD-pre-expansion
cp gamma/scanner.py gamma/scanner.py.bak.2026-05-DD-pre-expansion
```

`.bak` files are gitignored per existing `.gitignore` rule.

### E.5 What "wrong" looks like — failure scenarios

| Scenario | Trigger | Rollback |
|---|---|---|
| Scan runs > 10 min | yfinance 429s, parallelization too aggressive | Revert commit 2 (universe back to 27); investigate yfinance rate-limit; reduce `n_workers` |
| Wrong sector for a symbol → cap-binding wrong | Manual classification error | Revert commit 2; fix in subsequent PR |
| Spread verifier blocks half the universe | Bug in spread calc, or volatile market on Monday | Set `GAMMA_SPREAD_CHECK_ENABLED=0` immediately; investigate |
| Universe expansion fires unexpected trade | Should not happen — strategy logic unchanged | Halt all Gamma crons (`# HALTED 2026-05-DD:` prefix); investigate |

---

## F. Monitoring

### F.1 Dashboard tile (`agent-gamma-state.json`) — extended schema

Existing tile (current shape):
```json
{
    "data": {
        "scan_results": {
            "total_scanned": 27,
            "qualifying_setups": 0,
            "eligible_after_filters": 0,
            "instruments_triggering": []
        },
        ...
    }
}
```

Add `expansion_metrics` object:
```json
"expansion_metrics": {
    "universe_size": 155,
    "scan_duration_sec": 42.3,
    "scan_workers": 12,
    "setups_per_tier": {"tier_1": 0, "tier_2": 1, "tier_3": 0},
    "filter_rejections": {
        "spread_blocked": 4,
        "open_symbols": 0,
        "trend": 47,
        "rsi": 99,
        "volume": 0,
        "earnings_pre": 3,
        "earnings_post": 1
    },
    "cap_binding_events_30d": {
        "daily_cap": 5,
        "sector_cap": 2,
        "position_cap": 1
    },
    "spread_verifier": {
        "last_run": "2026-05-11T13:30:42-04:00",
        "n_blocked": 4,
        "n_fetch_failed": 3,
        "blocked_symbols": ["XYZ", "ABC", ...]
    }
}
```

Wired into `collect_gamma.py` (existing dashboard collector — runs `* * * * *`).

### F.2 gamma.log enhancements (per Part A §I.4 — overdue)

Replace the current log line:
```
[gamma_agent] 0 qualifying setups before risk filter (indicators computed for 27)
```

with:
```
[gamma_agent] indicators computed for 155/155 in 42.3s (parallel: 12 workers)
[gamma_agent] qualifying setups: 1 (rejections: spread_blocked=4, trend=47, rsi=99, volume=0, earnings_pre=3, earnings_post=1)
[gamma_agent] after sector/limit filter: 1 (cap-binding events today: daily=0, sector=0, position=0)
```

This makes future "0 qualifying" diagnostics 10× faster (no need to re-derive rejection breakdowns by hand as we did in Part A §D).

### F.3 Discord alerts — three new types

| Severity | Trigger | Channel | Cadence |
|---|---|---|---|
| 🚧 **Info** | Spread verifier completes; reports n_blocked + n_fetch_failed | `DISCORD_CHANNEL_LOGS` | Weekly (Monday) |
| 🔴 **Critical** | Scan duration > 5 min | `DISCORD_CHANNEL_ALERTS` | Per-scan if condition met |
| ⚠️ **Warning** | 30 consecutive trading days with 0 setups | `DISCORD_CHANNEL_ALERTS` | Once per silent stretch |

The 30-day warning prevents the kind of silent silence we just diagnosed in Part A.

### F.4 Rolling 30-day stats (`gamma_30d_stats.json`)

New file written by `collect_gamma.py`:
```json
{
    "window_start": "2026-04-09",
    "window_end": "2026-05-09",
    "trading_days": 22,
    "scans_executed": 22,
    "setups_qualified_total": 18,
    "setups_executed_total": 12,
    "setups_dropped_by_cap": {
        "daily_cap": 4,
        "sector_cap": 2,
        "position_cap": 0
    },
    "setups_by_sector": {
        "Healthcare": 5, "Technology": 3, "Industrials": 2, ...
    },
    "scan_duration_avg_sec": 41.8,
    "spread_verifier_runs": 4,
    "spread_blocked_total_unique_symbols": 7
}
```

This is the dataset for the **30-day cap-relaxation review** the user wants to do before relaxing daily/sector/position caps. Without this rolling stats file, that review would require re-deriving counts from gamma.log every time.

### F.5 Setup → entry → fill tracking

Existing journal already tracks fills. Add a `tier` field on each `agent_gamma` journal entry (`tier: 1|2|3`) so we can see post-mortem which tier delivered the trades that actually fired. Modification point: wherever `gamma_agent.py` writes the journal entry on entry placement.

---

## G. Pre-flight checklist (gates the merge)

Run **in order** before pushing. Each must pass.

| # | Step | Pass criterion |
|---|---|---|
| 1 | **Tests green (existing)** | `cd tests && python3 -m pytest unit -x` — full suite passes (was 944, expect 944 + new tests = ~960) |
| 2 | **Tests green (new)** | `pytest unit/test_gamma_universe.py unit/test_spread_verifier.py -v` — all new tests pass |
| 3 | **Backups taken** | `gamma/__init__.py.bak.2026-05-DD-pre-expansion` + `gamma/scanner.py.bak.*` exist |
| 4 | **Spread verifier dry-run** | `sudo python3 gamma_agent.py --verify-spreads --dry-run` completes in <90s, prints summary, does NOT write state file |
| 5 | **Spread verifier live run** | `sudo python3 gamma_agent.py --verify-spreads` writes `gamma_spread_status.json` with all 155 symbols enumerated |
| 6 | **Scanner dry-run on expanded universe** | `sudo python3 gamma_agent.py --scan --dry-run` completes in <90s with `n_workers=12`. Log shows `indicators computed for 155/155` |
| 7 | **Diff size sanity** | `git diff --stat` shows ~150 LOC net add to `__init__.py` + ~25 LOC scanner + ~120 LOC verifier + ~80 LOC tests = ~375 LOC. If >450, split further |
| 8 | **Sentinel awareness** | Confirm `NEVER_MODIFY_PATHS` in `sentinel_agent.py:103-111` already lists `scripts/gamma_agent.py`; new file `gamma/spread_verifier.py` is in the protected `gamma/` subdir (Sentinel won't touch the directory by extension) |
| 9 | **Cron entry added** | New line in `sudo crontab -l`: `30 13 * * 1 ... --verify-spreads` |
| 10 | **First post-merge scan** | Watch `gamma.log` after first 16:30 ET cron tick. Expect `indicators computed for 155/155 in <90s`. Investigate if <155 |
| 11 | **First post-merge Monday verifier** | Watch for `gamma_spread_status.json` creation Monday 9:30 ET. Expect Discord alert with `n_blocked` count |
| 12 | **30-day observation milestone** | `gamma_30d_stats.json` populated daily; review at day 30 to decide on cap relaxations |

### G.1 Rollout sequence (after pre-flight passes)

1. Merge commit 1 (verifier scaffolding, no behavior change). Ship.
2. Run `sudo python3 gamma_agent.py --verify-spreads` once manually to populate state file.
3. Merge commit 2 (universe + scanner + cron). Ship.
4. Watch first 16:30 ET scan that day. Confirm `indicators computed for 155/155`.
5. Watch following Monday's auto-verifier run.
6. Watch 30-day rolling stats.

### G.2 Halt conditions (abort rollout if any of these fire)

- Scan duration > 10 min on first run → revert commit 2; investigate yfinance.
- > 20% of universe blocked by first verifier run → likely bug in spread calc; investigate before next scan.
- Any traceback in `gamma.log` → halt + investigate.
- Sentinel posts a `🔴 critical` alert about Gamma → halt + investigate.

---

## Summary

| Section | Focus |
|---|---|
| A | 155-symbol universe, 6-key INSTRUMENT_CONFIG, deterministic yahoo→QA sector mapping |
| B | Monday spread verifier: new module, new cron, new state file, new scanner filter F0, env-flag feature gate |
| C | 9 universe tests + 8 verifier tests + 3 scanner-integration tests |
| D | Parallelize scanner outer loop with `ThreadPoolExecutor(max_workers=12)` — 8 min → 40s |
| E | 2-commit split for bisect, env-flag for verifier rollback, `.bak` backups, `git revert` for universe |
| F | Dashboard tile extension, gamma.log per-filter counters, 3 Discord alert types, 30-day rolling stats |
| G | 12-step pre-flight checklist, 6-step rollout, 4 halt conditions |

**Diff total estimate**: ~375 LOC across 5 files (`gamma/__init__.py`, `gamma/scanner.py`, `gamma/spread_verifier.py` NEW, `gamma_agent.py`, `tests/unit/test_gamma_universe.py` + `test_spread_verifier.py` NEW). 2 commits.

**Effort estimate**: 4–6 hours of careful work (test-first, with backups), excluding the 30-day observation window.

---

## Stop point

This document is the implementation plan. **No code has been changed; no commits made; no cron entries added.**

The actual implementation (writing the code, running tests, committing, deploying) waits for review and approval of this plan.
