# Phase 2 Item #1 — Reflection Memory + Universal Diagnostic

**Date:** 2026-05-09  
**Status:** PLAN — awaiting review before implementation

---

## A. Survey of Current State

### A1. Every Close Path by Agent

| # | Path | File:Lines | Trigger | Post-close hooks fire? |
|---|------|-----------|---------|------------------------|
| 1 | Broker already closed (no active legs) | position_monitor.py:1303-1320 | Exit rule + build_closing_legs() empty | YES — diagnose + review |
| 2 | Order filled + verified flat | position_monitor.py:1404-1433 | Exit rule + close order fills + verify_legs_flat | YES — diagnose + review |
| 3 | Partial scale-out | position_monitor.py:1388-1402 | scale_out_at, trailing_stop, vix_cross | NO — intentional (not a full close) |
| 4 | Manual close scripts | close_intc_wings.py, close_intc_ghosts.py | Operator emergency | NO — no hooks |
| 5 | Working order (not yet closed) | position_monitor.py:1343-1349 | Order submitted but not filled | N/A — trade stays OPEN |
| 6 | Verification fails | position_monitor.py:1357-1383 | Close order filled but broker verification fails | N/A — trade stays OPEN |

**Critical gap:** Paths 1 and 2 are the only production close paths. Both already fire diagnose + review hooks. The issue is that these hooks fire on only ~14% of closes because **the hooks themselves fail silently** (20s timeout per hook, exception swallowed). The gap is not "hooks don't fire" — it's "hooks fire but LLM calls fail and produce nothing."

**Partial closes:** Scale-outs (Path 3) write to `_partial_close_log` on the trade. These don't warrant full reflection — the trade is still open. No action needed.

**Manual scripts:** One-off emergency scripts. We'll document that any future manual close MUST call the reflection hook. Not worth retrofitting the existing scripts (they closed trades months ago).

### A2. Where Trajectory Data Lives

**Problem:** Bull/bear/judge trajectory data is NOT persisted to the trade journal.

At entry time, `debate_chamber.py` writes to `/root/quantai-v2/shared-data/cache/debate_output.json`:
```
{proposal, bull_case, bear_case, judge_score, judge_reasoning}
```

Then `autonomous_execution.py` reads this file and journals the trade, but **only persists**:
- `decision.thesis`, `decision.key_risk`, `decision.invalidation`
- `decision.conviction_score`
- Regime/VIX/data-freshness metrics

**NOT persisted:** `bull_case`, `bear_case`, `judge_score`, `judge_reasoning`.

The cache file lives only until the next debate run (~4-6 hours). By close time (typically days later), the trajectory is gone.

**Fix:** Extend the Alpha journal write in `autonomous_execution.py` to persist trajectory fields on the trade record at entry time.

### A3. Current Hook Sequence in position_monitor.py

```
rewrite_journal_atomic(updates) → SUCCESS →
  post_close_alert(Discord)
  try: agent_self_diagnosis.diagnose(trade_id)    # 20s timeout
  try: trade_reviewer.review(trade_id)            # 20s timeout
```

Both hooks are sequential, each in its own try/except. Failure of one does not prevent the other. Both swallow exceptions.

### A4. Why Diagnostics Fire on Only ~14% of Closes

Root cause: **LLM timeout/failure during hook execution.** The hooks DO fire on every close (Paths 1+2), but:
- 20s timeout is tight for Haiku calls that may hit ClawRoute latency
- Before the `_llm_call.py` hardening, a single failure = no result journaled
- No retry logic existed in these hooks until this session's Phase 2 Item #2

**Now fixed (partially):** `_llm_call.py` adds retry + backoff. But trade_reviewer.py's effective timeout is still 20s × 3 retries = 60s total, which may exceed position_monitor's tolerance.

**Full fix (this plan):** The reflection hook replaces the current trade_reviewer flow. A single unified hook writes the reflection. If LLM fails, it writes a stub and queues for nightly retry.

---

## B. Schema Decisions

### B1. Reflection Record Schema (JSONL)

```jsonc
{
  "trade_id": "A025",
  "agent": "agent_alpha",
  "ticker": "SPY",
  "strategy": "iron_condor",
  "regime_at_entry": "normal",
  "entry_features": {
    "vix": 16.5,
    "iv_rank": 42,
    "conviction_score": 7,
    "underlying_price": 555.23
  },
  "decision_summary": "Iron condor on SPY. Range-bound thesis, VIX 16.5.",
  "full_trajectory": {
    "bull_case": "4-5 bullet points...",
    "bear_case": "4-5 bullet points...",
    "judge_reasoning": "Selected because...",
    "judge_score": 74,
    "invalidation_clause": "SPY breaks 558 or 552.",
    "skills_consulted": ["regime-classification"]
  },
  "realized_return_raw": -42.50,
  "realized_return_pct": -1.7,
  "alpha_vs_spy": -0.8,
  "reflection_text": "The iron condor thesis was invalidated by...",
  "reflection_status": "complete",
  "closed_at": "2026-05-09T16:00:00-04:00",
  "close_reason": "stop_loss",
  "hold_days": 3
}
```

### B2. Gamma Structured Schema (NO LLM)

```jsonc
{
  "trade_id": "G001",
  "agent": "agent_gamma",
  "ticker": "AAPL",
  "strategy": "rsi_pullback_debit_spread",
  "regime_at_entry": "uptrend",
  "entry_features": {
    "rsi_10": 22.5,
    "sma_200_distance_pct": 3.2,
    "sector": "technology",
    "vix": 15.8,
    "conviction_score": 7
  },
  "decision_summary": "Connors RSI(10) pullback on AAPL. RSI 22.5, 3.2% above SMA(200).",
  "full_trajectory": null,
  "realized_return_raw": 145.00,
  "realized_return_pct": 12.3,
  "alpha_vs_spy": 0.5,
  "reflection_text": null,
  "reflection_status": "complete",
  "closed_at": "2026-05-12T09:45:00-04:00",
  "close_reason": "rsi_exit_above_40",
  "hold_days": 5,
  "gamma_structured": {
    "entry_rsi_bin": "20-25",
    "exit_rsi": 42.3,
    "exit_reason_category": "signal",
    "sector": "technology",
    "day_of_week_entry": "Monday",
    "day_of_week_exit": "Friday"
  }
}
```

### B3. Failure/Stub Schema

```jsonc
{
  "trade_id": "A026",
  "agent": "agent_alpha",
  "ticker": "GOOGL",
  "strategy": "bull_put_spread",
  "regime_at_entry": "normal",
  "entry_features": { ... },
  "decision_summary": "...",
  "full_trajectory": null,
  "realized_return_raw": -80.0,
  "realized_return_pct": -3.2,
  "alpha_vs_spy": null,
  "reflection_text": null,
  "reflection_status": "llm_failed",
  "reflection_error": "call_llm_text exhausted retries: TimeoutError",
  "closed_at": "2026-05-09T...",
  "close_reason": "stop_loss",
  "hold_days": 2,
  "retry_count": 0,
  "first_attempt_ts": "2026-05-09T..."
}
```

**`reflection_status` enum:** `"complete"` | `"llm_failed"` | `"pending_retry"` | `"manual_review"`

### B4. Trajectory Persistence Strategy

**Where it goes:** Extend the trade journal record at entry time.

In `autonomous_execution.py`, add to the journal entry:
```python
entry["full_trajectory"] = {
    "bull_case": trade.get("bull_case", ""),
    "bear_case": trade.get("bear_case", ""),
    "judge_reasoning": trade.get("judge_reasoning", ""),
    "judge_score": trade.get("judge_score"),
    "invalidation_clause": trade.get("invalidation", ""),
    "skills_consulted": (trade.get("decision") or {}).get("skills_consulted", []),
}
```

In `beta_agent.py`, add to the journal entry:
```python
entry["full_trajectory"] = {
    "bull_case": None,   # Beta has no debate
    "bear_case": None,
    "judge_reasoning": None,
    "judge_score": None,
    "invalidation_clause": _invalidation,
    "skills_consulted": ["regime-classification", "iv-surface-reading", "greeks-management"],
}
```

In `gamma_agent.py`:
```python
entry["full_trajectory"] = None  # Gamma has no debate
```

**Why journal, not sidecar:** The journal already has 20+ fields per trade. Adding `full_trajectory` (~1-3 KB) keeps it self-contained. At close time, `position_monitor.py` reads the full trade record including trajectory — no secondary lookup needed.

**Pre-existing trades:** Trades already in the journal without `full_trajectory` → treated as `null`. Reflection hook handles gracefully.

---

## C. Retrieval Design

### C1. Function Signatures

```python
def get_lessons(
    agent: str,
    symbol: str,
    k_same: int = 5,
    k_cross: int = 5,
) -> list[dict]:
    """Retrieve last K reflections for prompt injection.
    
    Returns up to k_same reflections on the same symbol + k_cross most-recent
    cross-symbol reflections. Only returns records with reflection_status == "complete".
    Reads from the agent's JSONL file. Returns newest first.
    """
```

### C2. Injection Format (into LLM prompt)

Injected as a markdown section appended to the proposal system prompt.

**Rule:** The most recent same-symbol lesson includes a `judge_reasoning` snippet from `full_trajectory` (~150 tokens) in addition to `reflection_text`. This surfaces the L1 trajectory data at retrieval time. All other lessons (older same-symbol + all cross-symbol) use reflection-text-only format.

```markdown
## Lessons from recent trades

### Same symbol (SPY):
- [A023] iron_condor, CLOSED stop_loss, -$42 (-1.7%), 3d hold:
  Judge reasoning: "Selected iron condor over diagonal due to low VIX (16.5)
  and 14-day range compression. Conviction 6/10 — modest. Key risk was CPI
  print in 2 days but assessed as priced in based on implied move."
  Reflection: "The iron condor thesis was invalidated by a post-CPI gap that
  exceeded the expected move. Should have checked event calendar proximity."

- [A019] bull_put_spread, CLOSED profit_target, +$180 (+7.2%), 5d hold:
  "Thesis confirmed — SPY support at 550 held. High conviction correct."

### Recent cross-symbol lessons:
- [A024] INTC iron_condor, CLOSED stop_loss, -$98 (-3.9%), 2d hold:
  "Concentration risk — 3rd INTC condor in a week. Cooldown gate would
  have prevented this."

- [A022] GOOGL bear_call_spread, CLOSED stop_loss, -$81 (-3.2%), 1d hold:
  "Re-entry too soon after prior GOOGL stop. Same thesis, same failure."
```

### C3. Token Budget

- Most recent same-symbol lesson: ~200-250 tokens (includes judge_reasoning snippet + reflection)
- Other 9 lessons: ~80-120 tokens each (reflection-text only)
- K=5 same + K=5 cross = 10 lessons max
- **Total budget: ~1130-1330 tokens** (~6% of a 2000-token proposal response window)
- This fits comfortably within the system prompt alongside CONSTITUTION and strategy descriptions

### C4. Retrieval Scope (per user correction)

| Agent | Writes reflections? | Reads reflections into live decisions? | Where reflections are consumed |
|-------|--------------------|-----------------------------------------|-------------------------------|
| Alpha | YES (every close) | YES — injected into proposal + judge prompts | debate_chamber.py |
| Beta | YES (every close) | NO — deterministic flow, no LLM in hot path | Dashboard, Friday digest, future regime calibrator |
| Gamma | YES (every close, structured) | NO — pure deterministic | Dashboard, threshold analyzer, Friday digest |

---

## D. Universal Diagnostic Coverage Fix

### D1. Root Cause

The existing hooks in position_monitor.py (diagnose + review) DO fire on every full close. The ~14% coverage is because:
1. Pre-hardening: single-shot LLM calls failed silently (~80% failure rate on Haiku during busy periods)
2. 20s timeout too tight for ClawRoute latency spikes

### D2. Solution

Replace the separate diagnose + review hooks with a single unified reflection hook:

```python
# In position_monitor.py, after journal write succeeds:
try:
    from _memory import write_reflection
    write_reflection(trade_id)
except Exception as e:
    logging.exception("Reflection hook failed for %s", trade_id)
```

`write_reflection()` internally:
1. Reads the closed trade from journal (including `full_trajectory`)
2. For Alpha/Beta: calls `call_llm_text()` to generate reflection (has 3-attempt retry built in)
3. For Gamma: builds structured reflection deterministically
4. Writes to agent JSONL
5. On ANY failure: writes stub with `reflection_status="llm_failed"`, logs to errors.db
6. NEVER raises

### D3. Nightly Reconciliation

New cron job: `reflection_reconciler.py` (runs 1x/night, 22:00 UTC)
- Scans all 3 JSONL files for records with `reflection_status != "complete"`
- For each stub: increment `retry_count`, attempt LLM call again
- After 3 calendar days of failed retries: set `reflection_status="manual_review"`, Discord alert
- Records with `reflection_status="manual_review"` are left for operator

### D4. Existing trade_reviewer.py and agent_self_diagnosis.py

**Keep them running.** They serve different purposes:
- `agent_self_diagnosis.py` writes `capability_diagnosis` to the trade record (feeds `quantai-learning.json` dashboard)
- `trade_reviewer.py` writes `post_trade` review to the trade record (feeds Friday synthesis)

The new reflection hook is **additive** — it writes to the separate JSONL files, not to the trade journal. The existing hooks continue to serve their dashboard/synthesis roles. This avoids breaking any existing consumers.

**Future simplification (not this PR):** Once reflections are proven reliable, consider whether trade_reviewer.py is redundant. For now: coexist.

---

## E. Test Plan

### E1. Unit Tests (`test_memory.py`)

1. `test_write_reflection_alpha_complete` — mock trade with trajectory, mock LLM returns text, verify JSONL written with all fields
2. `test_write_reflection_beta_complete` — same for Beta (trajectory has null bull/bear)
3. `test_write_reflection_gamma_structured` — verify NO LLM call, structured fields populated
4. `test_write_reflection_llm_fails_writes_stub` — mock LLM returns None, verify stub written with `reflection_status="llm_failed"`
5. `test_write_reflection_missing_trajectory_degrades` — trade has no `full_trajectory` field, verify reflection still written with `full_trajectory: null`
6. `test_write_reflection_missing_pnl_degrades` — trade has no pnl field, verify write succeeds with `realized_return_raw: null`
7. `test_write_reflection_never_raises` — mock disk error, verify function returns without raising
8. `test_get_lessons_same_symbol` — write 10 reflections (5 SPY, 5 AAPL), query SPY k_same=5, verify returns 5 SPY
9. `test_get_lessons_cross_symbol` — verify k_cross returns non-matching symbols, newest first
10. `test_get_lessons_skips_incomplete` — write mix of complete + llm_failed, verify only complete returned
11. `test_get_lessons_empty_file` — file doesn't exist, returns []
12. `test_get_lessons_fewer_than_k` — only 2 available, returns 2
13. `test_schema_validation` — verify all required fields present in output
14. `test_gamma_never_calls_llm` — monkeypatch `call_llm_text` to raise, verify Gamma reflection still succeeds
15. `test_alpha_vs_spy_calculation` — verify computation from trade pnl vs SPY return

### E2. Integration Tests (`test_memory_integration.py`)

1. `test_full_close_reflect_retrieve_loop` — write trade to tmp journal, call write_reflection, call get_lessons, verify round-trip
2. `test_multiple_agents_independent_files` — Alpha and Beta reflections don't cross-contaminate
3. `test_nightly_reconciler_retries_stub` — write stub, run reconciler logic, verify retry
4. `test_reflection_after_position_monitor_close` — simulate the full position_monitor close sequence with mocked broker

### E3. Test Constraints

- Gamma tests MUST verify `call_llm_text` is never called (monkeypatch to raise)
- Alpha tests with missing trajectory MUST still succeed (degrades, doesn't fail)
- All tests use tmp_path for JSONL files (no real disk state)

---

## F. Files Affected

### New Files

| File | Purpose |
|------|---------|
| `v2/shared-data/scripts/_memory.py` | Core module: `write_reflection()`, `get_lessons()`, `_build_reflection_prompt()` |
| `v2/shared-data/scripts/reflection_reconciler.py` | Nightly cron: retry failed reflections |
| `v2/shared-data/tests/unit/test_memory.py` | Unit tests (~15 tests) |
| `v2/shared-data/tests/integration/test_memory_integration.py` | Integration tests (~4 tests) |

### Modified Files

| File | Change |
|------|--------|
| `v2/shared-data/scripts/autonomous_execution.py` | Add `full_trajectory` to journal entry at write time; add `judge_score` to entry dict |
| `v2/shared-data/scripts/beta_agent.py` | Add `full_trajectory` to journal entry |
| `v2/shared-data/scripts/gamma_agent.py` | Add `full_trajectory: null` to journal entry |
| `v2/shared-data/scripts/position_monitor.py` | Add `write_reflection(trade_id)` call after journal rewrite succeeds |
| `v2/shared-data/scripts/debate_chamber.py` | Add lessons injection to proposal + judge system prompts via `get_lessons()` |

### New Runtime Files (created automatically)

| Path | Content |
|------|---------|
| `/root/quantai-v2/shared-data/memory/alpha_reflections.jsonl` | Alpha reflections |
| `/root/quantai-v2/shared-data/memory/beta_reflections.jsonl` | Beta reflections |
| `/root/quantai-v2/shared-data/memory/gamma_reflections.jsonl` | Gamma reflections |

### Cron Addition

```
# Reflection reconciler (nightly 22:00 UTC = 18:00 ET)
0 22 * * * reflection_reconciler.py
```

---

## G. Risks

### G1. What Breaks If We Get This Wrong

| Risk | Impact | Mitigation |
|------|--------|------------|
| Reflection hook raises during position_monitor close | Trade is already closed + journaled (hook fires AFTER journal write). No trade impact. Reflection lost. | try/except wrapping. Stub written on failure. |
| LLM generates hallucinated/incorrect reflection | Bad lesson injected into future debates → worse proposals | Reflection is 1 paragraph among 10 lessons, capped at ~5% of prompt. Low influence per-lesson. Operator reviews via dashboard. |
| JSONL file grows unbounded | Disk full (years away at 1.5 MB/year) | Not a real risk at current scale. Monitor file size in system_test.py. |
| Lessons injection makes proposal prompt too long | Token overflow or degraded quality | Hard cap at K=5+5=10 lessons, ~1200 tokens. Well within Sonnet's 200K context. |
| Trajectory persistence bloats trade journal | Slower journal reads | ~2KB per trade × 500 trades/year = 1MB. Negligible. |
| Nightly reconciler generates reflection for stale trade (context changed) | Reflection quality slightly lower | Acceptable — better to have a slightly-stale reflection than none. |

### G2. Edge Cases in Close Paths

| Edge Case | Handling |
|-----------|----------|
| Partial fill on close order | Trade stays OPEN until fully flat. Reflection fires only on full close (verify_legs_flat). |
| Manual close via IBKR TWS | position_monitor detects via polling (Path 1: broker already closed). Hooks fire. `full_trajectory` may be null if trade predates this change → graceful degradation. |
| Corrupted journal entry (malformed JSON line) | `write_reflection()` reads trade via `find_trade()` which skips malformed lines. If trade not found → write stub with `reflection_status="trade_not_found"`. |
| Trade closed before this code ships (pre-existing A001-A025, B001-B003) | No `full_trajectory` on record → reflection written with `full_trajectory: null`, `decision_summary` built from available fields only. |
| Two position_monitor cycles try to close same trade | Atomic journal rewrite prevents double-close. Second cycle sees trade already CLOSED → no hook fires. |
| Close during market hours + LLM slow (60s reflection) | position_monitor cycle delayed by up to ~60s. Acceptable — next cycle runs 2 min later. No trading impact. |

### G3. The debate_output.json Race Condition

Between Alpha entry and Alpha close, the debate_output.json is overwritten many times. This is why we persist `full_trajectory` to the journal at entry time, not read it at close time. Once on the journal record, it's immutable.

---

## H. Known Future Upgrades (DO NOT implement now)

1. **Vector similarity retrieval (Item #7, Phase 4):** Replace `get_lessons()` last-N filter with ChromaDB embedding search. JSONL stays source of truth; vector index is a read-accelerator built from it.
2. **Composite trust score (Items #4+#6 merged):** Consume reflections to compute win rates per (strategy, regime). Not in this PR.
3. **Operator feedback correlation:** Link Friday operator replies to specific reflection records. Separate scope.
4. **Reflection quality scoring:** After 50+ reflections, evaluate whether injected lessons actually improve proposal quality (A/B test). Not now.

---

## I. Implementation Order

1. Write unit tests for `_memory.py` (test-first)
2. Implement `_memory.py` (write_reflection + get_lessons)
3. Run unit tests green
4. Modify `autonomous_execution.py` — persist trajectory at entry time
5. Modify `beta_agent.py` — persist trajectory at entry time
6. Modify `gamma_agent.py` — persist trajectory (null) at entry time
7. Modify `position_monitor.py` — add reflection hook after journal write
8. Modify `debate_chamber.py` — inject lessons into proposal + judge prompts
9. Implement `reflection_reconciler.py` (nightly retry — safety net must exist before failures can accumulate)
10. Write integration tests
11. Run full suite green
12. Commit + push

---

## J. Verification

1. Full test suite (1226+ tests) must pass
2. Dry-run Alpha pipeline: verify `full_trajectory` appears on journaled entry
3. Simulate a close (mock broker): verify JSONL reflection written
4. Simulate a close with LLM failure: verify stub written, no exception raised
5. Call `get_lessons("agent_alpha", "SPY")` on file with 10+ reflections: verify correct filtering
6. Inspect debate_chamber.py prompt: verify lessons section appears, doesn't exceed token budget
7. Verify Gamma path: JSONL written with structured data, NO Sonnet call in logs
