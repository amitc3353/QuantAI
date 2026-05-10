# Gamma 4-Arm A/B/C/D Ranking Test — Implementation Plan (2026-05-10)

**Status**: Final implementation plan. **No code changes yet.** One more review required before commit 1.

**Companion documents**:
- `docs/gamma-silence-diagnosis-2026-05-09.md` (Part A — diagnosed why Gamma fired zero trades since 2026-04-30)
- `docs/gamma-universe-expansion-proposal.md` (Part B — research proposal for universe expansion)
- `docs/gamma-universe-expansion-implementation-plan.md` (universe-expansion implementation plan)

**Goal**: Run a 4-arm A/B/C/D ranking experiment on Gamma's setup-selection logic. Each arm is a fully-isolated virtual portfolio with $10K starting capital, its own ranker, its own positions, its own caps and circuit breaker. After ≥60 days (extendable to 180), compare realized P&L + Sharpe and promote the winner.

**Hard constraints** (enforced throughout):
- **Signal logic UNCHANGED across all arms.** RSI<30, SMA(200) trend filter, earnings 7/2-day blackout, 1M-share liquidity floor — all identical. This is a RANKING test, not a signal test.
- **Cap values UNCHANGED.** Daily-cap-of-2, sector-cap-of-2, position-cap-of-3 are the same numbers across all arms (each arm has its OWN counters, but the cap value is identical).
- **Strike selector UNCHANGED.** DTE 14–21, reward:risk floor 0.8.
- **Universe stays at 155.** No additions during the experiment (frozen — see §G).
- **Compounding mode**: position-sizing scales with arm's current equity (decision in §D, defended).

---

## User-decided design choices (locked-in)

1. **Sample-size floor: 80 trades/arm.** Accept up to 180-day run. Realistic end date: ~November 7, 2026.
2. **Compounding mode** (not constant notional). Defended in §D.
3. **Per-arm files authoritative + trades.jsonl as union** for backwards compat. Defended in §E.
4. **Clean restart on ranker bug** (not partial). Defended in §G.
5. **Each arm books its own fill price** (no post-fill averaging). Defended in §C.
6. **Skip-all on partial broker failure.** Defended in §G.
7. **Test config freeze during experiment** (universe, caps, ranker logic). Defended in §G.
8. **Drop VIX dampener from Arm B.** Pure 4-factor composite, no regime multiplier. Defended in §A.
9. **Add Arm D = REWARD_RISK_FIRST** to force divergence from RSI-correlated A/B/C. Defended below.

---

## The four arms

### Arm A — RSI_ONLY (control, Connors-canonical baseline)
- Sort qualifying setups by `rsi_10` ascending.
- Tiebreak: alphabetical by symbol.
- This is the existing Gamma logic, unchanged.
- Trade ID prefix: `Ga###`.

### Arm B — COMPOSITE (multi-factor weighted score)
- Score each setup as a weighted sum of within-day min-max-normalized factors:
  - **45% × RSI level** (lower RSI = higher score; we use `1 - normalized_rsi`)
  - **30% × spread reward:risk ratio** (higher r:r = higher score, normalized 0–1)
  - **15% × distance above SMA(200)** (more cushion = higher score, normalized 0–1)
  - **10% × distance above SMA(50)** (recent trend strength, normalized 0–1)
- **No VIX dampener.** (Removed per user review — see "drop VIX dampener" reasoning below.)
- Sort by composite score descending.
- Trade ID prefix: `Gb###`.

### Arm C — WEIGHTED_BLEND (ensemble of A and B)
- For each qualifying setup, compute its rank in Arm A AND its rank in Arm B.
- `blend_rank = (rank_a + rank_b) / 2`.
- Sort blend_rank ascending.
- Tiebreak: prefer the setup with lower RSI.
- Trade ID prefix: `Gc###`.

### Arm D — REWARD_RISK_FIRST (added in user review for divergence)
- Sort by spread reward:risk descending.
- Tiebreak: lower RSI wins (when reward:risk is equal).
- Trade ID prefix: `Gd###`.
- **Rationale**: Arms A, B, and C are all heavily correlated to RSI (A is pure RSI, B has 45% RSI weight, C blends A and B). Without Arm D, the test risks measuring four nearly-identical rankers. Arm D is intentionally divergent: it picks setups primarily for their EXPECTED-PROFIT structure (best reward:risk first), not for the depth of the RSI pullback. This tests whether "best spread economics" beats "deepest oversold" as a ranking philosophy.

### Why drop the VIX dampener from Arm B?

The original spec had Arm B multiply its final score by 0.7 if VIX > 25, else 1.0. We dropped this for two reasons:

1. **Arbitrary parameters muddy the test.** We have no evidence that 25 is the right VIX threshold or 0.7 is the right multiplier. If Arm B wins, we want to know whether the 4-factor composite philosophy won — not whether a guessed regime adjustment won. The dampener could swing results based on which side of VIX 25 the regime sits in during the test window.
2. **Test the philosophy first; tune adjustments later with evidence.** If composite ranking wins this test, we ship it as-is and gather a year of data. If a VIX dampener helps in retrospect, it gets added later as a follow-up evolution with evidence.

If composite (Arm B) wins, follow-up work could add a regime adjustment based on observed VIX-conditional win rates — but only with data.

---

## A. ARCHITECTURE

### Ranker abstraction

Strategy pattern via Python Protocol + a registry. Each ranker takes the qualifying-setup list (output of `scan_with_indicators()`) plus a context dict (VIX, scan timestamp, today) and returns the same setups ordered best-first with two attached fields:
- `_rank` (1-indexed position in the order)
- `_score` (numeric, higher = better, for debugging/logging)

```python
# gamma/rankers/__init__.py (NEW)
from typing import Protocol

class Ranker(Protocol):
    name: str
    def rank(self, qualifying_setups: list[dict], context: dict) -> list[dict]: ...

# gamma/rankers/rsi_only.py (NEW)
class RsiOnlyRanker:
    name = "rsi_only"
    def rank(self, setups, context):
        ranked = sorted(setups, key=lambda s: (s["rsi_10"], s["symbol"]))
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            s["_score"] = -s["rsi_10"]
        return ranked

# gamma/rankers/composite.py (NEW)
class CompositeRanker:
    name = "composite"
    WEIGHTS = {"rsi": 0.45, "reward_risk": 0.30, "dist_sma200": 0.15, "dist_sma50": 0.10}
    def rank(self, setups, context):
        # 1. Min-max normalize each factor across qualifying set
        # 2. Compute weighted score (RSI inverted)
        # 3. Sort score descending; attach _rank, _score
        ...

# gamma/rankers/weighted_blend.py (NEW)
class WeightedBlendRanker:
    name = "weighted_blend"
    def __init__(self):
        self.a = RsiOnlyRanker()
        self.b = CompositeRanker()
    def rank(self, setups, context):
        a_ranked = self.a.rank([dict(s) for s in setups], context)
        b_ranked = self.b.rank([dict(s) for s in setups], context)
        a_rank_by_sym = {s["symbol"]: s["_rank"] for s in a_ranked}
        b_rank_by_sym = {s["symbol"]: s["_rank"] for s in b_ranked}
        for s in setups:
            s["_blend_rank"] = (a_rank_by_sym[s["symbol"]] + b_rank_by_sym[s["symbol"]]) / 2.0
        ranked = sorted(setups, key=lambda s: (s["_blend_rank"], s["rsi_10"]))
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            s["_score"] = -s["_blend_rank"]
        return ranked

# gamma/rankers/reward_risk_first.py (NEW)
class RewardRiskFirstRanker:
    name = "reward_risk_first"
    def rank(self, setups, context):
        # Higher reward_risk_estimate is better; lower RSI tiebreaks
        ranked = sorted(setups, key=lambda s: (-s["reward_risk_estimate"], s["rsi_10"]))
        for i, s in enumerate(ranked):
            s["_rank"] = i + 1
            s["_score"] = s["reward_risk_estimate"]
        return ranked

RANKERS: dict[str, Ranker] = {
    "rsi_only": RsiOnlyRanker(),
    "composite": CompositeRanker(),
    "weighted_blend": WeightedBlendRanker(),
    "reward_risk_first": RewardRiskFirstRanker(),
}

ARM_TO_RANKER = {
    "a": "rsi_only",
    "b": "composite",
    "c": "weighted_blend",
    "d": "reward_risk_first",
}
```

### Normalization

Min-max within the day's qualifying set:
```python
def _norm(value, vmin, vmax):
    if vmax == vmin:
        return 0.5  # all equal — neutral score
    return (value - vmin) / (vmax - vmin)
```

For RSI specifically (lower = better), use `1 - _norm(rsi, rsi_min, rsi_max)` so that the lowest RSI gets the highest score.

### Reward:risk pre-rank computation

Today, `build_spread()` (`gamma/strike_selector.py:51`) computes reward:risk during EXECUTE, after picks are made. To use reward:risk as a pre-rank factor for Arms B and D, we need a cheap pre-pass.

**Decision**: extract a `compute_reward_risk_estimate(setup, broker)` helper that runs at SCAN time. It uses:
- The current `close` from the indicator cache
- A standard ATM-long / 5%-OTM-short strike heuristic
- Live IBKR option chain quotes for the closest 14–21 DTE expiry

Cost: ~0.3s/symbol via parallel `ThreadPoolExecutor`. For ~5 qualifying symbols/day, ~1.5s added to scan. Acceptable.

The full `build_spread()` still runs at execute time; this estimate is for ranking only. **Caveat**: the estimate may differ from actual fill r:r (which uses different mid-quote, possibly different strikes). All four arms see the SAME estimate, so the test comparison is fair.

### Existing scan_with_indicators flow extension

Current call site in `gamma_agent.py:163`:
```python
setups, indicator_cache = scan_with_indicators(UNIVERSE, open_symbols=open_symbols)
```

New flow under `GAMMA_AB_TEST_ENABLED=1`:
```python
setups, indicator_cache = scan_with_indicators(UNIVERSE, open_symbols=set())  # no per-arm filtering yet
# Compute reward:risk estimate for every qualifying setup (parallel)
setups = compute_reward_risk_estimates(setups, broker)
context = {"vix": load_vix(), "scan_ts": now_iso(), "today": today}

for arm_id in ("a", "b", "c", "d"):
    arm_state = load_arm_state(arm_id)
    arm_open = arm_open_positions(arm_state)
    arm_setups = [s for s in setups if s["symbol"] not in arm_open]  # F1: skip already-held
    ranker = RANKERS[ARM_TO_RANKER[arm_id]]
    ranked = ranker.rank([dict(s) for s in arm_setups], context)
    eligible = filter_setups_for_arm(ranked, arm_id, arm_state, journal)
    write_arm_pending(arm_id, eligible[:MAX_DAILY_ENTRIES])
    log_ranking_decisions(arm_id, ranked, eligible, picked=eligible[:MAX_DAILY_ENTRIES])
```

The divergence point is `ranker.rank()`. Everything upstream (scanner, indicator cache, earnings filter, volume filter, reward:risk estimate) is shared.

When `GAMMA_AB_TEST_ENABLED=0`, behavior reverts to single-ranker (Arm A) using the existing `filter_setups()` against the union journal — this is the emergency-fallback path.

---

## B. PER-ARM PORTFOLIO ENGINE

### Per-arm state files

Path: `/root/quantai-v2/shared-data/cache/gamma_arm_<a|b|c|d>_account.json`

Schema:
```json
{
    "arm_id": "a",
    "ranker_used": "rsi_only",
    "starting_equity": 10000.00,
    "current_equity": 10342.18,
    "peak_equity": 10580.00,
    "drawdown_pct": -2.25,
    "total_realized_pnl": 342.18,
    "total_trades": 14,
    "winning_trades": 9,
    "losing_trades": 5,
    "consecutive_losses": 0,
    "circuit_breaker_active": false,
    "circuit_breaker_until": null,
    "last_trade_close_ts": "2026-05-22T15:42:18-04:00",
    "last_updated": "2026-05-23T16:30:42-04:00",
    "experiment_started_at": "2026-05-11T09:33:42-04:00",
    "experiment_day": 12
}
```

Atomic writes via tempfile + `os.replace()`.

### Per-arm circuit breaker

`circuit_breaker_active=true` blocks new entries for that ARM ONLY. Other arms continue. Triggered when `consecutive_losses >= 3`. Cleared 48h after `last_trade_close_ts`.

### Per-arm position monitor extension

Existing `position_monitor.py:check_gamma_exit()` (line 508-575) handles exit logic per position. Doesn't need to change.

The dispatch loop (line 963) iterates broker positions. Extension: each Gamma position's journal entry has an `arm_id` field. On exit:
1. Update the per-arm state file (P&L, equity, consecutive_losses, circuit_breaker if applicable).
2. Write to the per-arm trade journal (`gamma_arm_<id>_trades.jsonl`).
3. Append to `trades.jsonl` (the union file) with `arm_id` set.

```python
# position_monitor.py extension
def _on_gamma_exit(trade, exit_info):
    arm_id = trade.get("arm_id")
    if not arm_id:
        # Pre-experiment trade or non-arm Gamma fallback
        legacy_close(trade, exit_info)
        return
    realized_pnl = exit_info["realized_pnl"]
    arm_state = load_arm_state(arm_id)
    arm_state["current_equity"] += realized_pnl
    arm_state["total_realized_pnl"] += realized_pnl
    arm_state["total_trades"] += 1
    if realized_pnl > 0:
        arm_state["winning_trades"] += 1
        arm_state["consecutive_losses"] = 0
    else:
        arm_state["losing_trades"] += 1
        arm_state["consecutive_losses"] += 1
        if arm_state["consecutive_losses"] >= 3:
            arm_state["circuit_breaker_active"] = True
            arm_state["circuit_breaker_until"] = (now() + timedelta(hours=48)).isoformat()
    arm_state["peak_equity"] = max(arm_state["peak_equity"], arm_state["current_equity"])
    arm_state["drawdown_pct"] = (arm_state["current_equity"] / arm_state["peak_equity"] - 1) * 100
    arm_state["last_updated"] = now_iso()
    save_arm_state(arm_id, arm_state)
    append_arm_trade(arm_id, trade, exit_info)  # gamma_arm_<id>_trades.jsonl
    append_union_trade(trade, exit_info)         # trades.jsonl (existing)
```

### Per-arm filter_setups

Extended signature in `gamma/risk_check.py`:
```python
def filter_setups_for_arm(setups: list[dict], arm_id: str,
                          arm_state: dict, journal: list) -> list[dict]:
    """Apply sector/daily/position caps using arm-scoped state.
    Filters journal to entries with source == f'agent_gamma_arm_{arm_id}'.
    Same cap values as legacy filter_setups; just scoped to one arm."""
```

Existing `filter_setups()` stays as a fallback for when the experiment is disabled. The new function delegates to the same underlying logic with arm-scoped journal/state.

---

## C. PER-ARM BROKER INTEGRATION

### Order tagging

`client_order_id` format extends from:
```
gamma-{date}-{symbol}
```
to:
```
gamma-arm-{a|b|c|d}-{date}-{symbol}
```

This makes per-arm order tracking trivial via IBKR's `clientOrderId`-based lookup.

### Same-fill-price handling — each arm books its own fill (no averaging)

When 2+ arms pick the same symbol same day, each arm submits its own order. Fills will likely be very close (< $0.01 spread variance) but not bit-identical.

**Decision: no post-fill averaging.** Each arm books its own fill price as journaled by IBKR.
- The variance is small enough (<0.5%) that Sharpe-level statistics dominate.
- Implementing averaging would require a synchronization barrier (wait for all fills, compute average, then record) that adds complexity for a marginal benefit.
- The original spec phrasing "use the average" is interpreted as describing INTENT (we want roughly equivalent fills); in practice, near-simultaneous submission produces de-facto-equivalent fills.

If the user observes >1% fill variance in production, we can add an averaging pass post-hoc. Until then, simple is better.

### Order failure per arm — skip-all

If Arm A's submit fails (IBKR error 201, "Cancelled" status, etc.) but B/C/D are working or filled, we cancel the others for that symbol. Implementation:

```python
results = []
for arm_id in arms_picking_symbol:
    r = submit_arm_order(arm_id, setup, broker)
    results.append((arm_id, r))

# Check for terminal failure on any arm
any_failed = any(r is None or (r.get("status") or "").lower()
                 in TERMINAL_FAILURE_STATUSES for _, r in results)
if any_failed:
    # Cancel still-working orders for this symbol
    for aid, r in results:
        if r and (r.get("status") or "").lower() in INDETERMINATE_STATUSES:
            cancel_order(r["order_id"])
    log_skipped_due_to_partial_failure(setup, results)
    post_discord(f"🔴 Gamma A/B/C/D divergence — {setup['symbol']} cancelled "
                 f"due to partial failure: {[(a, r.get('status') if r else 'None') for a,r in results]}")
```

This keeps the ranking-comparison clean: every arm that participated had its decision honored uniformly.

---

## D. CAPITAL ALLOCATION

**$10K per arm × 4 arms = $40K total paper capital.**

### Per-arm parameters (read at scan time from arm's state file)

- `account_size = arm.current_equity` (compounding mode)
- `max_risk_per_trade = arm.current_equity * 0.01` (1% per trade, scales)
- `max_open_positions = 3`
- `max_daily_entries = 2`
- `max_positions_same_sector = 2`

### Compounding vs constant notional — defended

**Compounding wins**:

The test's purpose is to identify which ranker we'll **ship**. Live trading uses compounding (1% of current equity); a constant-notional test would optimize for ranker quality in isolation but tell us less about the ranker we'd actually run. Compounding amplifies early luck, but at 80+ trades per arm, the law of large numbers significantly dampens this. The 15% P&L margin requirement plus the Sharpe gate require the winner to demonstrate consistent performance, not a single lucky stretch.

Constant notional has a hidden cost: it uses position sizing math that doesn't reflect the actual deployed system. We'd be measuring the wrong system.

### Per-arm state files: schema, atomic writes, reconciliation

Schema documented in §B. Writes happen at:
- Trade entry (decrement available cash by max_risk; increment total_trades)
- Trade exit (adjust current_equity by realized P&L; update consecutive_losses, circuit breaker)
- Daily reconciliation pass at 16:35 ET (after scan, computes drawdown, peak)

Reconciliation: on every read, check `last_updated` timestamp. If stale (>24h), log warning. If sum of (open trade max_risk) + cash differs from current_equity by more than $1.00, log error and post Discord. Catches subtle accounting drift.

### IBKR paper account caveat — confirmed correct approach

The broker only knows total account balance (DUP851506 ≈ $1M paper equity). Per-arm equity is QuantAI-side accounting maintained in JSON state files independently of broker account balance. The arms SHARE the same broker account; the $10K/arm is purely a virtual partition.

If all four arms together hold positions worth $5K, the broker shows $5K in positions on the $1M account. Our state files split this into "Arm A: $1500, Arm B: $1500, Arm C: $1000, Arm D: $1000" based on arm tags.

Position tagging via `arm_id` in journal makes this reconciliable. If reconciliation fails (e.g., a position has no arm_id), we log and treat as legacy.

**Edge case**: the IBKR paper account itself could have issues (force-liquidation in extreme markets, vanishingly rare). All four arms get hit identically. Acceptable test risk; documented.

---

## E. SCHEMA

### Per-arm trade journal entry

`gamma_arm_a_trades.jsonl`:
```json
{
    "id": "Ga001",
    "arm_id": "a",
    "ranker_used": "rsi_only",
    "rank_at_entry": 1,
    "rank_total_qualifying": 3,
    "score_at_entry": -28.5,
    "factor_breakdown_at_entry": {
        "rsi_10_raw": 28.5, "rsi_10_normalized": 1.0,
        "reward_risk_raw": 1.85, "reward_risk_normalized": 0.6,
        "dist_sma200_raw": 12.3, "dist_sma200_normalized": 0.4,
        "dist_sma50_raw": 8.7, "dist_sma50_normalized": 0.7,
        "vix_at_scan": 17.4
    },
    "timestamp": "2026-05-12T09:33:42-04:00",
    "mode": "paper",
    "source": "agent_gamma_arm_a",
    "symbol": "AAPL",
    "strategy": "rsi_pullback_debit_spread",
    "legs": [...],
    "order_id": "...",
    "fill_status": "Filled",
    "filled_qty": 1,
    "avg_fill_price": ...,
    "net_debit": 1.85,
    "spread_width": 5.0,
    "max_risk": 185.0,
    "max_profit": 315.0,
    "reward_risk": 1.70,
    "qty": 1,
    "total_risk": 185.0,
    "total_risk_pct": 1.85,
    "rsi_at_entry": 28.5,
    "sma_200_distance_pct": 12.3,
    "vix_at_entry": 17.4,
    "decision": {...},
    "status": "OPEN",
    "arm_equity_at_entry": 10000.0
}
```

For Arm A: `factor_breakdown_at_entry` records raw values; only `rsi_10_normalized` is computed (others null).
For Arm B: all four normalized values present; `score_at_entry` is the composite weighted sum.
For Arm C: `score_at_entry` is the negative blended rank; `factor_breakdown_at_entry` includes `rank_a` and `rank_b`.
For Arm D: `score_at_entry` is the reward:risk estimate; `factor_breakdown` records the raw r:r and tiebreak RSI.

### Master ranking_decisions.jsonl

Path: `/root/quantai-v2/shared-data/logs/gamma_ranking_decisions.jsonl`

One entry per scan:
```json
{
    "scan_timestamp": "2026-05-12T16:30:42-04:00",
    "vix_at_scan": 17.4,
    "n_qualifying": 5,
    "qualifying_symbols": ["AAPL", "TMO", "JNJ", "GILD", "PEP"],
    "factor_raw": {
        "AAPL": {"rsi_10": 28.5, "reward_risk_estimate": 1.85, "dist_sma200": 12.3, "dist_sma50": 8.7},
        ...
    },
    "factor_normalized": {
        "rsi_only": {"AAPL": {"rsi_10_norm": 1.0}, ...},
        "composite": {"AAPL": {"rsi_norm": 1.0, "rr_norm": 0.6, "sma200_norm": 0.4, "sma50_norm": 0.7}, ...},
        "weighted_blend": {"AAPL": {"rank_a": 1, "rank_b": 1}, ...},
        "reward_risk_first": {}
    },
    "scores_per_arm": {
        "a": [{"symbol": "AAPL", "score": -28.5}, ...],
        "b": [{"symbol": "AAPL", "score": 0.78}, ...],
        "c": [{"symbol": "AAPL", "score": -1.0}, ...],
        "d": [{"symbol": "AAPL", "score": 1.85}, ...]
    },
    "ranks_per_arm": {
        "a": ["AAPL","TMO","JNJ","GILD","PEP"],
        "b": ["JNJ","AAPL","PEP","TMO","GILD"],
        "c": ["AAPL","JNJ","TMO","PEP","GILD"],
        "d": ["TMO","JNJ","AAPL","PEP","GILD"]
    },
    "picked_per_arm": {"a": ["AAPL","TMO"], "b": ["JNJ","AAPL"], "c": ["AAPL","JNJ"], "d": ["TMO","JNJ"]},
    "dropped_per_arm": {
        "a": [{"symbol":"JNJ","reason":"ranked_3rd_below_daily_cap"}, ...],
        "b": [{"symbol":"TMO","reason":"sector_cap_healthcare_already_2_open"}, ...],
        "c": [...],
        "d": [...]
    }
}
```

This is the audit log. Every decision the experiment made is reconstructible from it. Critical for forensic debugging and for the day-60+ promotion analysis.

### Trade ID generation per arm

Each arm has its own counter. Implementation:
```python
def next_arm_trade_id(arm_id: str, journal: list) -> str:
    prefix = f"G{arm_id}"
    max_n = 0
    for t in journal:
        tid = t.get("id") or ""
        if tid.startswith(prefix) and tid[len(prefix):].isdigit():
            try:
                max_n = max(max_n, int(tid[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{max_n + 1:03d}"
```

Counter scopes to per-arm journal file so Ga001 and Gb001 are independent.

### Backwards compat: trades.jsonl

Every per-arm journal write also appends to `trades.jsonl` with `arm_id` set. Existing tooling (sentinel reclassify, error_learner, weekly_synthesis, dashboard collectors, position_monitor, ~12 callers) reads trades.jsonl unchanged — the new `arm_id` field is additive and ignored by existing code.

The trade ID prefix change (`G001` → `Ga001`/`Gb001`/`Gc001`/`Gd001`) is the only schema-visible change. Tools that filter on `tid.startswith("G")` still work. Tools that parse the digit suffix need to update — confirm via grep before commit 2.

---

## F. WIN CRITERIA (PRE-COMMITTED — copy verbatim from user spec)

Test runs for **minimum 60 calendar days**. After day 60, evaluate.

Promotion rules in order of precedence:

1. **SAMPLE SIZE FLOOR**: each arm must have ≥ 80 trades. If any arm has < 80, extend the test by 30 days. Repeat at day 90, 120, 150 if still short. Hard cap at day 180; if any arm still has < 80 trades, **declare test inconclusive and ship Arm A as default**.

2. **WIN MARGIN**: best arm must beat runner-up by ≥ 15% in **total realized P&L** AND have **equal-or-better Sharpe ratio**. If yes, promote.

3. **NEAR-TIE FALLBACK**: if best two arms are within 5% on P&L, ship the **simpler** one. Simplicity order: A (RSI_ONLY) > D (REWARD_RISK_FIRST) > B (COMPOSITE) > C (WEIGHTED_BLEND). Ockham's razor applied to the 4-arm field.

4. **INCONCLUSIVE BAND**: if margins are between 5% and 15%, extend by 30 days and re-evaluate. Hard cap at 180 days.

These criteria are committed PRE-test; we follow the rules; we don't move the goalposts.

### Sharpe ratio computation

Per arm, on closed trades only:
- Daily return series = realized P&L on day T / equity at start of day T
- Annualized Sharpe = (mean_daily × 252) / (stdev_daily × sqrt(252)) using risk-free rate = 0
- Computed at evaluation points (day 60, 90, 120, 150, 180)

### Realistic timeline

- Day 0: Monday 2026-05-11 (or first market day after pre-flight passes)
- Day 60: ~July 10, 2026
- Day 90: ~August 9, 2026
- Day 120: ~September 8, 2026
- Day 150: ~October 8, 2026
- **Day 180 (hard cap): ~November 7, 2026**

Per the user-decided sample-size feasibility analysis: the test is likely to run to day 90 or 120 minimum due to Gamma's natural firing rate (1.3 setups/day across all arms uncapped, lower with daily-cap-2). The 80-trade floor with 4 arms = 320 total trades, which at ~1.5/day average = ~213 trading days = ~10 calendar months. **Plan for the test to consume the full 180-day window.**

---

## G. FAILURE MODES & MID-TEST RECOVERY

### Bug found in one arm's ranker mid-test → CLEAN RESTART

All four arms reset to $10K, archive old data, restart day 0. Operator runs:
```bash
sudo python3 gamma_agent.py --reset-experiment --reason="<short description>"
```

Script:
1. Posts to Discord asking for confirmation (✓ via reaction).
2. On confirm: zeroes all four `gamma_arm_<id>_account.json` to $10K start.
3. Archives per-arm journals: `archive/gamma_arm_<id>_<old_start>_to_<reset_date>.jsonl`.
4. Archives ranking_decisions.jsonl: `archive/gamma_ranking_decisions_<old_start>_to_<reset_date>.jsonl`.
5. Logs reset reason to a new file: `archive/experiment_resets.jsonl`.
6. Resets day-0 timestamp on next live scan.

Reasoning: the whole point of pre-committed criteria is to prevent post-hoc rationalization. Patching a ranker mid-test pollutes the data.

### Broker connection issue causes one arm's order to fail → SKIP-ALL for symbol

Submit all arms' orders for the symbol within 5 seconds. Wait briefly for terminal status. If any arm's order fails, cancel all working orders on that symbol. Discord alert. The trade doesn't happen for ANY arm that day. Sample-size loss is small (rare event).

### Universe expansion or other Gamma config change → FREEZE

Frozen during experiment (until completion or reset):
- `UNIVERSE` list
- `INSTRUMENT_CONFIG` (sectors)
- `RSI_ENTRY_THRESHOLD`
- `LIQUIDITY_MIN_VOLUME`
- `EARNINGS_BLACKOUT_DAYS` / `EARNINGS_POST_DAYS`
- `DTE_MIN` / `DTE_MAX`
- `MAX_DAILY_ENTRIES`, `MAX_POSITIONS_SAME_SECTOR`, `MAX_OPEN_POSITIONS`
- The 4 ranker implementations

Allowed during test:
- Logging/observability improvements
- Spread verifier behavior (blocked symbols change weekly — that's part of the test environment, all arms see the same blocklist)
- Bug fixes outside ranking logic (broker, position_monitor exit logic, dashboard rendering)

**Permitted maintenance during freeze (added per user review 2026-05-10):**
- **Symbol delistings**: if a symbol gets delisted from the exchange, no action is needed — the symbol simply stops producing setups (yfinance returns no data, scanner skips it). This is data evolution, not a test parameter change. The universe list stays unchanged; the symbol becomes a no-op.
- **Pure ticker renames** (e.g., FB → META): update the `INSTRUMENT_CONFIG` mapping in place, keeping the same sector + tax classification. This is admin maintenance to track the same underlying instrument under its new ticker, NOT a universe addition. The renaming arm has no impact on test variables.

All other universe/cap/ranker changes trigger clean restart.

If a frozen item MUST change (security issue, etc.): clean restart per the bug rule.

### Paper account broker disconnect → PAUSE ALL ARMS TOGETHER

Existing IBKR connection check at the broker level handles this. If `connect()` fails, all four arms' submit calls fail; all four skip that day's entries. Resume together when broker recovers.

### Emergency stop

Env var `GAMMA_AB_TEST_ENABLED=0` disables the entire experiment. Scanner reverts to single-ranker (Arm A) behavior. Existing per-arm positions remain open and managed via `position_monitor.py` until they exit via normal triggers; new entries route through Arm A only.

Use case: catastrophic bug, regulatory issue, or operator decision.

---

## H. REPORTING

### Daily collector (`collect_gamma.py` extended)

Reads all 4 arm state files + reconciles open positions per arm. Writes to dashboard state:

```json
{
    "experiment_active": true,
    "experiment_day": 14,
    "experiment_started": "2026-05-11T09:33:42-04:00",
    "experiment_eta_eval": "2026-07-10",
    "experiment_eta_hard_cap": "2026-11-07",
    "arms": {
        "a": {"name": "RSI_ONLY", "current_equity": 10342, "trades": 12, "win_rate": 0.67, "sharpe": 1.4, "drawdown": -2.0},
        "b": {"name": "COMPOSITE", "current_equity": 10580, "trades": 11, "win_rate": 0.73, "sharpe": 1.8, "drawdown": -1.5},
        "c": {"name": "WEIGHTED_BLEND", "current_equity": 10260, "trades": 13, "win_rate": 0.62, "sharpe": 1.1, "drawdown": -3.2},
        "d": {"name": "REWARD_RISK_FIRST", "current_equity": 10410, "trades": 14, "win_rate": 0.64, "sharpe": 1.3, "drawdown": -2.8}
    },
    "today_picks_per_arm": {"a": ["AAPL"], "b": ["JNJ", "AAPL"], "c": ["AAPL"], "d": ["TMO"]},
    "divergence_rate_30d": 0.31,
    "all_agree_rate_30d": 0.18,
    "circuit_breakers_active": {"a": false, "b": false, "c": false, "d": false},
    "scan_duration_sec": 42.3,
    "reward_risk_estimator_duration_sec": 1.8,
    "n_qualifying_setups_today": 5
}
```

**Timing instrumentation (added per user review 2026-05-10)**: `compute_reward_risk_estimates()` is wrapped in a timer. The per-scan duration is surfaced as `reward_risk_estimator_duration_sec` in the dashboard tile, alongside `scan_duration_sec`. **Watchdog rule**: if a high-setup-day's total scan duration (scanner + estimator) exceeds 60s, that's a signal to investigate parallelization headroom. The estimator alone shouldn't exceed ~2s for the typical 5-setup day, so >5s is also a red flag worth investigating.

### Weekly Discord digest (Friday 4:30 PM ET)

```
📊 Gamma A/B/C/D Test — Week 4 of ≥9 (likely 26)
─────────────────────────────────────────────────
Day 28 of minimum 60. ETA to first eval: day 60 (Jul 10).
Hard cap: day 180 (Nov 7).

  Arm A (RSI_ONLY):           $10,342 | 12 trades | 67% win | Sharpe 1.4
  Arm B (COMPOSITE):          $10,580 | 11 trades | 73% win | Sharpe 1.8
  Arm C (WEIGHTED_BLEND):     $10,260 | 13 trades | 62% win | Sharpe 1.1
  Arm D (REWARD_RISK_FIRST):  $10,410 | 14 trades | 64% win | Sharpe 1.3

Cumulative P&L lead: B > D > A > C

Trade overlap (all 4 arms picked same): 18% of trade-days
Divergence rate (some arm picked differently): 31% of trade-days
Notable divergences this week:
  • TMO (Tue): only B and D picked it, +$87 / +$87 win
  • XLK (Thu): A and C picked, B and D skipped (ranked 3rd/4th), +$45 each
  • GILD (Fri): only D picked it, -$120 loss

Sample size projection: at current rate (~3.0 trades/arm/week),
all arms hit 80-trade floor around day 165. Test will likely
run to 180-day hard cap (Nov 7).
```

### Dashboard tile

4-column layout in `/var/dashboard/index.html`:

| Arm A: RSI_ONLY | Arm B: COMPOSITE | Arm C: WEIGHTED_BLEND | Arm D: REWARD_RISK_FIRST |
|---|---|---|---|
| Equity: $10,342 | Equity: $10,580 | Equity: $10,260 | Equity: $10,410 |
| Trades: 12 | Trades: 11 | Trades: 13 | Trades: 14 |
| Win rate: 67% | Win rate: 73% | Win rate: 62% | Win rate: 64% |
| Sharpe: 1.4 | Sharpe: 1.8 | Sharpe: 1.1 | Sharpe: 1.3 |
| Today: [AAPL] | Today: [JNJ, AAPL] | Today: [AAPL] | Today: [TMO] |

Below the columns: equity-curve overlay (4 lines, color-coded). Time series sparkline showing day-over-day equity change.

Banner at top of dashboard: "🧪 Gamma 4-Arm Test Active — Day X of ≥60. Promotion eval at day 60 if all arms ≥80 trades. Hard cap day 180."

---

## I. TESTS

### Ranker correctness (`tests/unit/test_gamma_rankers.py` NEW)

- `test_rsi_only_ranks_by_rsi_ascending()` — tiebreak alphabetical
- `test_composite_score_formula()` — given hand-computed inputs, score matches expected weighted sum
- `test_composite_normalization_within_day()` — same setup ranks differently with different peer set
- `test_composite_no_vix_dampener()` — confirm VIX is NOT used (correction from review)
- `test_weighted_blend_averages_ranks()` — given known A and B ranks, blend matches
- `test_weighted_blend_tiebreak_lower_rsi()` — equal blended ranks → lower RSI wins
- `test_reward_risk_first_ranks_by_rr_descending()` — primary order
- `test_reward_risk_first_tiebreak_lower_rsi()` — equal r:r → lower RSI wins
- `test_normalization_handles_min_eq_max()` — single-symbol or all-equal case → 0.5 default
- `test_ranker_attaches_rank_and_score_fields()` — output has `_rank`, `_score` on every entry
- `test_ranker_does_not_mutate_input_setups()` — input list unchanged (each ranker copies)

### Per-arm portfolio isolation (`tests/unit/test_gamma_arm_state.py` NEW)

- `test_arm_state_load_save_roundtrip()`
- `test_per_arm_equity_isolated()` — Arm A trade doesn't affect Arm B/C/D equity
- `test_per_arm_circuit_breaker_isolated()` — only one arm hits the 3-loss threshold; others continue
- `test_per_arm_sector_cap_isolated()` — Arm A's 2 healthcare positions don't block Arm B's healthcare entry
- `test_compounding_position_sizing()` — equity changes scale next trade's max_risk
- `test_state_file_atomic_write()` — temp+rename pattern; partial-write doesn't leave broken file
- `test_state_file_reconciliation_alert()` — sum-of-positions ≠ equity → log + Discord
- `test_starting_equity_after_reset()` — clean restart zeroes back to $10K

### Schema backwards compat (`tests/unit/test_gamma_journal_compat.py` NEW)

- `test_per_arm_journal_has_arm_id()`
- `test_trades_jsonl_union_includes_all_arms()`
- `test_trade_id_prefix_per_arm()` — Ga001 / Gb001 / Gc001 / Gd001 increment per arm
- `test_existing_tools_can_read_with_arm_id()` — sentinel reclassify, error_learner, weekly_synthesis parse new field without error
- `test_legacy_gamma_trades_still_parseable()` — old G001-G003 format pre-experiment still readable

### Feature flag toggle (`tests/unit/test_gamma_ab_flag.py` NEW)

- `test_flag_off_uses_single_ranker()` — `GAMMA_AB_TEST_ENABLED=0` → only Arm A behavior, single trades.jsonl source, single state
- `test_flag_on_routes_to_four_arms()`
- `test_flag_change_at_runtime_handled()` — operator flips flag; new scans use new mode; existing positions managed normally
- `test_flag_off_does_not_touch_arm_state_files()` — flag-off pass leaves arm state files untouched

### Edge cases

- `test_same_symbol_four_arm_overlap()` — all 4 arms pick AAPL, all 4 get virtual positions
- `test_one_arm_picks_zero_today()` — Arm A picked 0 (caps hit), others still pick
- `test_partial_broker_failure_skips_all()` — Arm A submit fails → cancel B, C, D for that symbol
- `test_circuit_breaker_blocks_only_affected_arm()` — Arm B circuit-broken, A/C/D continue trading

### Win criteria evaluation (`tests/unit/test_gamma_promotion_logic.py` NEW)

- `test_sample_size_floor_extends_test()` — any arm < 80 → extend
- `test_15pct_margin_promotes_winner()` — clear winner promotes
- `test_15pct_margin_but_lower_sharpe_does_not_promote()` — Sharpe gate enforced
- `test_5pct_near_tie_picks_simpler()` — A vs B within 5% → A wins (simpler)
- `test_5pct_near_tie_d_vs_b_picks_d()` — D vs B within 5% → D wins (simpler)
- `test_inconclusive_band_extends()` — 7% margin → extend
- `test_180_day_hard_cap_ships_arm_a()` — at 180d still inconclusive → A wins as default

**Total**: ~40 new tests across 5 new test files (~10 more than prior 3-arm plan due to Arm D coverage).

---

## J. IMPLEMENTATION PHASING (5 commits)

Note: the Arm D ranker is added in commit 1 alongside A/B/C — they share the same scaffolding so splitting "Arm D" into a 6th commit doesn't add bisect value. 5 commits remain.

### Commit 1: ranker abstraction + 4 implementations + tests (no behavior change)
- New: `gamma/rankers/__init__.py`, `gamma/rankers/rsi_only.py`, `gamma/rankers/composite.py`, `gamma/rankers/weighted_blend.py`, `gamma/rankers/reward_risk_first.py`
- New: `gamma/reward_risk_estimator.py` — pre-rank reward:risk computation helper
- New: `tests/unit/test_gamma_rankers.py`
- Existing scanner unchanged. Feature flag default OFF. Production behavior unchanged.
- Estimated: ~470 LOC code + ~280 LOC tests = **~750 LOC**

### Commit 2: per-arm state tracking + journals (no behavior change)
- New: `gamma/arm_state.py` (load/save state files, atomic writes, reconciliation)
- New: per-arm journal files (initialized empty: `gamma_arm_a/b/c/d_trades.jsonl`)
- New: per-arm state files (initialized at $10K each)
- New: `tests/unit/test_gamma_arm_state.py`, `test_gamma_journal_compat.py`
- Existing journal-write code unchanged.
- Estimated: ~280 LOC code + ~250 LOC tests = **~530 LOC**

### Commit 3: per-arm broker routing + position monitoring
- Modified: `gamma_agent.py` (run_scan + run_execute extended for 4-arm dispatch behind feature flag; `--reset-experiment`, `--promote-arm` subcommands added)
- Modified: `position_monitor.py` (per-arm exit dispatch; arm_id-aware close path)
- Modified: `gamma/risk_check.py` (`filter_setups_for_arm()`)
- New: `tests/unit/test_gamma_arm_orchestration.py`, `test_gamma_ab_flag.py`
- Feature flag still default OFF. With flag on, behavior changes to 4-arm dispatch.
- Estimated: ~400 LOC code + ~200 LOC tests = **~600 LOC**

### Commit 4: reporting + dashboard + win-criteria evaluator
- Modified: `collect_gamma.py` (per-arm aggregation, divergence rate)
- New: `gamma_weekly_digest.py` (4-arm digest format) — separate file rather than bundling into weekly_synthesis.py for clean ownership
- Modified: `dashboard/index.html` (4-column tile + equity overlay + experiment banner)
- New: `gamma/promotion_evaluator.py` — encodes win criteria from §F
- New: `tests/unit/test_gamma_promotion_logic.py`
- Estimated: ~380 LOC code + ~200 LOC tests = **~580 LOC**

### Commit 5: feature flag flip + experiment activation
- Trivial: `.env` adds `GAMMA_AB_TEST_ENABLED=1` (was 0 default)
- Sentinel awareness update: add `gamma/rankers/`, `gamma/arm_state.py`, `gamma/reward_risk_estimator.py`, `gamma/promotion_evaluator.py` to `NEVER_MODIFY_PATHS`
- Run pre-flight checklist (§K)
- Day 0 starts when this commit lands
- Estimated: ~10 LOC

**Total estimated diff**: ~1540 LOC code + ~930 LOC tests = **~2,470 LOC across ~16 files in 5 commits.**

This is roughly 2.5× the size of the universe expansion (1100 + 500 = 1600 LOC). Reasonable scope for a 6-month experiment with proper bisect safety.

---

## K. ROLLOUT

### Pre-flight checklist (run before commit 5 / feature flag flip)

| # | Step | Pass criterion |
|---|---|---|
| 1 | Tests green (full suite) | `pytest unit -x` — 100% pass, expecting ~1327 (existing 1287 + 40 new) |
| 2 | Tests green (new only) | `pytest unit/test_gamma_rankers.py unit/test_gamma_arm_state.py unit/test_gamma_journal_compat.py unit/test_gamma_ab_flag.py unit/test_gamma_arm_orchestration.py unit/test_gamma_promotion_logic.py -v` — all pass |
| 3 | Backups taken | `gamma_agent.py.bak.YYYY-MM-DD-pre-experiment`, `position_monitor.py.bak.*`, `gamma/risk_check.py.bak.*`, `gamma/__init__.py.bak.*`, `collect_gamma.py.bak.*` |
| 4 | All 4 rankers manual dry-run | `python3 gamma_agent.py --scan --dry-run --abtest=on` produces ranking_decisions.jsonl entry with all 4 arms ranked correctly; no exceptions |
| 5 | State files initialized | All 4 `gamma_arm_<a/b/c/d>_account.json` exist with `current_equity=10000.00`, `total_trades=0`, `experiment_started_at` set |
| 6 | Master ranking_decisions.jsonl bootstrap | File exists, parsable JSON-lines |
| 7 | Per-arm journals bootstrap | 4 empty `gamma_arm_<id>_trades.jsonl` files exist |
| 8 | Reward:risk estimator dry-run | Manual call against 5 sample symbols returns reasonable r:r values (0.5–3.0 range) |
| 8b | **Reward:risk estimator parity check (added 2026-05-10)** | Run `compute_reward_risk_estimate()` against 20 fixed symbols at scan time, then run `build_spread()` against the same symbols at execute time, compare reward:risk values. Document the median absolute difference. **If > 15%, investigate before proceeding to commit 1's downstream commits.** Also implemented as automated unit test (`test_estimator_vs_build_spread_median_delta`) so any regression is caught in CI. |
| 9 | Dashboard tile renders | 4-column layout displays correctly with $10K each, 0 trades, no positions |
| 10 | Discord digest dry-run | Manual `gamma_weekly_digest.py --dry-run` produces correctly formatted week-0 summary |
| 11 | Sentinel awareness | `gamma/rankers/`, `gamma/arm_state.py`, `gamma/reward_risk_estimator.py`, `gamma/promotion_evaluator.py` added to NEVER_MODIFY_PATHS |
| 12 | Freeze list confirmed | Operator acknowledges via Discord that UNIVERSE, caps, ranker logic are frozen for the experiment duration |
| 13 | Feature flag default OFF in source | `.env` doesn't have GAMMA_AB_TEST_ENABLED=1 yet — final commit flips to 1 |
| 14 | Promotion evaluator dry-run | Manual `python3 gamma_agent.py --evaluate-promotion --dry-run` runs against synthetic 4-arm-result fixtures and produces correct decision (test extension / promote / inconclusive) per §F rules |

### Day 0 (experiment start, target Monday 2026-05-11)

Feature flag flips to 1. All four arms begin trading. Morning EXECUTE picks first batch. Discord posts "🧪 Gamma A/B/C/D Test — Day 0 — all arms initialized at $10K. ETA hard cap: Nov 7, 2026."

### Day 30 (mid-point, ~June 10, 2026)

Discord digest. Sanity checks:
- Divergence rate sane (>20%)?
- Sample-size projection on track?
- Any arms in circuit-breaker?
- Any state-file reconciliation alerts in the past 30 days?

NO promotion decisions yet.

### Day 60 (first eval, ~July 10, 2026)

Run `gamma_agent.py --evaluate-promotion`. Possible outcomes:
- All arms ≥ 80 trades + clear winner with 15%+ margin AND ≥ Sharpe → **promote**
- All arms ≥ 80 trades + 5–15% margin → extend 30d
- All arms ≥ 80 trades + < 5% margin → ship simpler arm (Ockham)
- Any arm < 80 trades → extend 30d

### Day 90 / 120 / 150 (extensions if needed)

Re-evaluate. Continue extending up to 180.

### Day 180 (hard cap, ~November 7, 2026)

Final evaluation per §F. If still inconclusive: ship Arm A as default. Operator must Discord-ack the choice with reasoning.

### Promotion day

1. Operator runs `python3 gamma_agent.py --promote-arm <a|b|c|d> --reason="..."`
2. Script:
   - Closes other 3 arms' positions at market
   - Archives per-arm journals to `archive/gamma_arm_*_final_<date>.jsonl`
   - Sets `GAMMA_AB_TEST_ENABLED=0`
   - Updates `gamma/__init__.py` to use winning ranker as the default in fallback path
   - Restores single-ranker behavior with the winning ranker as the live decision maker
3. Discord posts: "🏆 Gamma A/B/C/D Test Concluded — Arm <X> Promoted — final P&L: A=$..., B=$..., C=$..., D=$..."
4. Master ranking_decisions.jsonl preserved at `/root/quantai-v2/shared-data/logs/gamma_ranking_decisions.jsonl`
5. Dashboard reverts to single-portfolio view (cleanup commit lands separately)

---

## L. RISKS

### Top risk: regime bias

The test runs in whatever regime exists during those 60+ days. If the regime is bull-market overbought (current state), Arm A's "lowest RSI first" might dominate because the few qualifying trades are all on rare oversold dips that any reasonable ranker would pick. If the regime is volatile/range-bound, Arm B's composite might shine because reward:risk varies more. Arm D ("best spread economics") may be regime-sensitive in unexpected ways.

**The winner of this test is conditional on the test's regime. We cannot generalize to all market conditions.**

Mitigation:
- Document this explicitly in the promotion announcement.
- Track VIX-at-scan in every ranking_decisions.jsonl entry; in the post-promotion analysis, partition results by regime.
- Revisit the chosen ranker annually; don't treat the result as permanent.

### Risk: small-sample noise

80 trades is a heuristic. Standard error of mean P&L per trade at n=80 with stdev=$50 is $5.6 — so a $14 difference between arms is at the noise floor. The 15% margin requirement helps but doesn't eliminate.

Mitigation: extension rules give us up to 6 months. Sharpe gate adds a second dimension (returns AND risk-adjusted) that's harder to luck into.

### Risk: bug masquerading as ranker quality

If Arm B's normalization has a subtle off-by-one, it could systematically prefer certain symbols over others, looking like "ranker quality" but actually a bug.

Mitigation:
- §I tests cover normalization edge cases.
- Code review of all 4 rankers BEFORE day 0.
- Parity test: Arm A's `_rank` field must match the existing `setups.sort()` behavior on legacy (pre-experiment) data.
- Day-1 manual review of the first ranking_decisions.jsonl entry.

### Risk: same-symbol overlap >> threshold

If all 4 arms agree on most picks, the test has minimal information content. Adding Arm D mitigates this — Arm D's reward:risk-first logic should diverge from RSI-first logic in non-trivial ways.

Mitigation: track divergence rate; if at day 30 it's < 20%, escalate to operator. Arm D was added specifically to defend against this risk.

### Risk: compounding amplifies an early bug

If Arm B has a bug that causes it to win a streak in week 1, its compounded equity is now significantly higher; even after we fix the bug, equity inequality persists.

Mitigation: clean restart on bug detection (§G). The pre-commit clean-restart rule is non-negotiable.

### Risk: implementation bug in critical path

Per-arm state tracking, journal-union write, broker tagging, reward:risk estimator — bugs in any of these corrupt the test.

Mitigation:
- §I tests + dry-run period before flag flip
- Post-day-1 manual review of first day's state files and ranking_decisions.jsonl
- Reconciliation alerts (sum-of-positions vs current_equity) catch silent drift
- Pre-flight checklist requires successful end-to-end dry-run

### Risk: capital allocation bug

If position-sizing math diverges between arms (one uses arm equity correctly, another uses total broker equity), comparison is invalid.

Mitigation: dedicated unit test asserts `max_risk_per_trade == arm.current_equity * 0.01` EXACTLY for each arm, including post-loss and post-win edge cases (§I).

### Risk: reward:risk estimator drift from actual fills

The pre-rank reward:risk estimate is a heuristic; actual fills may produce different r:r. If the estimator systematically overstates r:r for one type of symbol, Arm B and Arm D get biased rankings.

Mitigation: log estimated AND actual r:r at execute time; weekly digest reports the discrepancy. If estimator drift > 10% systematically, escalate.

### Risk: 180-day hard cap reached without enough trades

Even with extensions, the 80-trade floor may not be hit by day 180 if the regime stays adverse. Decision: ship Arm A as default in that case (per §F.1). This is a conservative fallback — Arm A is the existing logic, so shipping it preserves the status quo.

---

## M. DIFF SIZE ESTIMATE & FILE LIST

### New files (~15)

| File | LOC est. |
|---|---|
| `gamma/rankers/__init__.py` (registry) | ~40 |
| `gamma/rankers/rsi_only.py` | ~30 |
| `gamma/rankers/composite.py` | ~120 |
| `gamma/rankers/weighted_blend.py` | ~80 |
| `gamma/rankers/reward_risk_first.py` | ~40 |
| `gamma/reward_risk_estimator.py` | ~150 |
| `gamma/arm_state.py` | ~180 |
| `gamma/promotion_evaluator.py` | ~120 |
| `gamma_weekly_digest.py` | ~180 |
| `tests/unit/test_gamma_rankers.py` | ~280 |
| `tests/unit/test_gamma_arm_state.py` | ~180 |
| `tests/unit/test_gamma_journal_compat.py` | ~120 |
| `tests/unit/test_gamma_ab_flag.py` | ~120 |
| `tests/unit/test_gamma_arm_orchestration.py` | ~180 |
| `tests/unit/test_gamma_promotion_logic.py` | ~180 |

### Modified files (~6)

| File | LOC est. |
|---|---|
| `gamma_agent.py` | +280 (per-arm scan dispatch, --reset-experiment, --promote-arm, --evaluate-promotion) |
| `position_monitor.py` | +100 (per-arm exit dispatch + arm_id-aware close) |
| `gamma/risk_check.py` | +90 (`filter_setups_for_arm()`) |
| `gamma/__init__.py` | +40 (ranker imports, ARM_TO_RANKER, GAMMA_AB_TEST_ENABLED reader, RANKER_DEFAULT) |
| `collect_gamma.py` | +100 (per-arm aggregation, divergence rate) |
| `dashboard/index.html` | +180 (4-column tile + equity overlay + experiment banner) |
| `sentinel_agent.py` | +5 (extend NEVER_MODIFY_PATHS) |
| `.env` | +1 (GAMMA_AB_TEST_ENABLED) |

### Total

- **Code: ~1,615 LOC**
- **Tests: ~1,060 LOC**
- **Total: ~2,675 LOC across 21 files in 5 commits**

This is bigger than the universe expansion (~1,500 LOC code+tests) by ~1.7×. Justified by the experiment scope (6-month commitment, $40K virtual capital, 4 independent portfolios).

---

## Stop point

This document is the implementation plan. **No code has been changed; no commits made.**

The actual implementation (5 commits per §J) waits for **one more review** of this plan + resolution of any final concerns before commit 1.

When approved, implementation will run through this weekend with day 0 of the experiment targeting Monday 2026-05-11 (or first market day after pre-flight checklist passes green).
