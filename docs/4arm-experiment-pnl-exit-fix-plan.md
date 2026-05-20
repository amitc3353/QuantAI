# 4-Arm Experiment — P&L/Exit Subsystem Fix Plan

## Status: RESTART SUSPENDED — Fix sequence in progress

**Commit 1 shipped** (22379bf, 2026-05-18): exit routing `startswith` fix + 18 tests.
Commits 2-5 pending. Restart blocked until all fixes land + USB phantoms resolved.

---

## Context

During pre-restart verification for the Gamma 4-arm A/B/C/D experiment, investigation
uncovered 6 interacting defects in position_monitor.py's P&L computation and exit logic.
These affect ALL agents (Alpha, Beta, Gamma), not just per-arm Gamma trades. The defects
must be fixed as a coordinated set before the experiment restart can proceed.

**Commit 1 (exit routing)** shipped 2026-05-18. The remaining fixes require careful
sequencing because they interact: the P&L clamp (Commit 3) depends on geometry
derivation which must handle paper-sim impossible fills (D4), and the Alpha unit
mismatch fix (Commit 2) is the highest operational urgency because A026 (OXY diagonal)
is live with no working stop-loss.

---

## Defect Catalog (D1–D6)

### D1 — Unbounded compute_trade_pnl()
**File:** `position_monitor.py` lines 237-250
**Impact:** All agents. Sums IBKR per-leg `unrealizedPNL` with no structural cap.
Multi-leg spread P&L can exceed structural max loss due to inter-leg pricing
inconsistency in paper sim. Produced -248% on a capped-loss debit spread.
**Evidence:** Gc001 trade, -$136.64 on $55 max_risk.

### D2 — Upstream unit inconsistency in scan_options.py, reflected downstream in position_monitor exit logic

**Root cause (upstream):** `scan_options.py` uses inconsistent unit conventions
across strategy scanners:
- `scan_verticals()` line 268: `credit = short_lastPrice - long_lastPrice` — **per-share, no ×100**
- `scan_iron_condors()` line 513-518: `total_credit = sum(short_premiums) - sum(long_premiums)` — **per-share, no ×100**
- `scan_diagonals()` line 395: `net_debit = (far_price - near_price) × 100` — **total-dollar, ×100 applied**

The debate chamber LLM sees these different-unit values in its context and generates
`estimated_credit` proposals that reflect the inconsistency. For iron condors, the
LLM echoes per-share credit (small positive values, 0.65–4.23). For diagonals, the
LLM invents `estimated_credit` from the total-dollar `net_debit` context and stores
a negative total-dollar value (−92 to −298), or sometimes zero.

`autonomous_execution.py` line 648 writes `estimated_credit` to the journal verbatim.
No unit normalization occurs anywhere in the pipeline.

**Downstream symptom (position_monitor.py lines 848-863):**
`pnl < -(2 * estimated_credit)` compares total-dollar P&L to a threshold whose
units depend on which strategy type produced the trade:
- Iron condors: threshold is per-share → fires at ~2-5% of actual risk (premature)
- Diagonals (negative credit): `abs()` gives total-dollar → threshold accidentally
  correct for 8/9 diagonal entries, but the code path uses `credit > 0` which is
  False for negative values, so the branch is **never entered** for diagonals
- Diagonals (A026, credit=0): branch skipped entirely → no P&L-based exit

**Verification (2026-05-20):**

| Strategy | estimated_credit unit | sign | ×100 to get total-$? | Journal entries |
|---|---|---|---|---|
| iron_condor | Per-share | Positive | **Yes — confirmed** | 16 entries (A007, A011-A025) |
| diagonal_spread | Total-dollar | Negative (debit) | **No — disproved** (would set stop at 100× max loss) | 8 entries (A002-A010) |
| diagonal_spread (A026) | Zero (LLM omission) | N/A | N/A | 1 entry |
| bull_put_spread | Design-time only | — | — | **0 entries — no journal data, no Commit 2 coverage needed** |
| bear_call_spread | Design-time only | — | — | **0 entries — no journal data, no Commit 2 coverage needed** |

The ×100 hypothesis is **confirmed for iron condors** and **formally disproved for
diagonals**. A blanket ×100 fix is incorrect. Any Commit 2 fix must either branch
on strategy type, detect the unit from the value, or be superseded by geometry-based
bounds (Commit 3).

### D3 — Alpha journal lacks structural fields
**File:** `autonomous_execution.py` lines 640-682
**Impact:** Alpha only. Journal entries store `estimated_credit` and `max_loss_pct`
but NOT `max_risk`, `net_debit`, or `net_credit`. The geometry-based P&L clamp
cannot use journal fields for Alpha — must derive from position geometry (legs).

### D4 — Paper-sim impossible fills — MAJORITY CONDITION for Alpha iron condors

**File:** `_broker_ibkr.py` (fill processing)
**Impact:** All agents on paper sim. IBKR paper sim produces fills where credit
exceeds wing width (structurally impossible on real exchange). This creates
nonsensical position geometry that defeats any structural bound derived from legs.

**Reclassification (2026-05-20):** D4 is NOT a rare edge case. Of 16 Alpha iron
condor journal entries, **9 (56%) have estimated_credit exceeding wing width**.
This is the majority condition for Alpha iron condors:

| Trade | Wing width | estimated_credit | credit > wing? |
|---|---|---|---|
| A007 | $2.50 | 3.40 | **YES** |
| A011 | $1.00 | 2.18 | **YES** |
| A012 | $5.00 | 4.23 | No |
| A013 | $0.50 | 0.89 | **YES** |
| A014 | $2.50 | 0.65 | No |
| A015 | $2.50 | 1.12 | No |
| A016 | $2.50 | 2.09 | No |
| A017 | $1.00 | 1.97 | **YES** |
| A018 | $1.00 | 2.64 | **YES** |
| A019 | $1.00 | 0.75 | No |
| A020 | $1.00 | 1.65 | **YES** |
| A021 | $1.00 | 1.74 | **YES** |
| A022 | $1.00 | 1.95 | **YES** |
| A023 | $1.00 | 1.37 | **YES** |
| A024 | $1.00 | 0.69 | No |
| A025 | $1.00 | 1.87 | **YES** |

**Implication for Commit 3:** Geometry-derived max-loss for 56% of iron condors
would be negative (wing_width - credit < 0), triggering the D4 degradation path
(no clamping). This significantly limits how much Commit 3's geometry clamp
actually protects Alpha iron condor trades. The clamp is effective only for the
44% of iron condors with valid geometry (A012, A014-A016, A019, A024) and for
diagonal spreads.

### D5 — Entry-write-before-fill
**File:** `gamma_agent.py` lines 1157-1162
**Impact:** Gamma per-arm trades. Journal OPEN + cash decrement happen at submission
time, not fill time. If order never fills → permanent phantom + locked cash.
**Evidence:** Gc001/Gd001 both have `fill_status: "Submitted"`, `filled_qty: 0`.

### D6 — Phantom detection with no terminal state
**File:** `position_monitor.py` lines 1044-1063
**Impact:** All agents. Phantom detection posts hourly Discord alerts but has no
escalation, no auto-resolution, no status change. 1,466 alerts over 5 days for
Gc001/Gd001 with zero resolution.

---

## Fix Sequence (5 Commits)

### Commit 1 — Exit routing fix ✅ SHIPPED
**Commit:** 22379bf (pushed to origin/main 2026-05-18)
**Files:** `position_monitor.py` (lines 521, 1193)
**Tests:** `test_gamma_exit_routing.py` (18 tests, 5 classes)
**Change:** `source == "agent_gamma"` → `source.startswith("agent_gamma")`

### Commit 2 — Alpha exit threshold fix (D2)
**Priority:** HIGHEST — A026 (OXY diagonal) is live with no working stop
**Scope:** Upstream unit inconsistency in scan_options.py reflected downstream
in position_monitor.py. The position_monitor change is downstream of the actual
bug (scan_options.py line 395 applying ×100 while lines 268/513-518 do not).

**Verification completed 2026-05-20:**
- ×100 hypothesis **confirmed** for iron condors (per-share → total-dollar)
- ×100 hypothesis **disproved** for diagonals (already total-dollar, ×100 would
  set stop at 100× max loss, effectively disabling it)
- bull_put_spread, bear_call_spread: **design-time only, zero journal entries,
  no Commit 2 coverage needed**
- A026 (diagonal, credit=0): LLM omission, not convention — no fix to
  estimated_credit produces a working stop for this trade

**Implementation approach:** Open decision — see Part B analysis below.
**Dependency:** See "Risk coupling with Commit 3" below.

### Commit 3 — Geometry-based P&L clamp (D1 + D3 + D4)
**Priority:** HIGH — protects all agents from impossible P&L booking
**File:** `position_monitor.py` — new function + integration into compute_trade_pnl
**Design:** Derive structural max-loss from position geometry (legs array):

1. **Identify spread type** from legs: vertical (same expiry, different strikes),
   diagonal (different expiry), calendar, iron condor (4 legs), naked.
2. **Compute structural max loss:**
   - Credit vertical: `(wing_width - net_credit) × 100`
   - Debit vertical: `net_debit × 100`
   - Iron condor: `(max_wing_width - net_credit) × 100`
   - Diagonal: `net_debit × 100` (approximate — true max loss is path-dependent)
3. **Clamp:** `pnl = max(pnl, -structural_max_loss)` after compute_trade_pnl returns.
4. **D4 degradation:** If derived geometry is nonsensical (credit > wing width,
   negative max loss), log warning and fall through WITHOUT clamping. Better to
   let the unclamped P&L flow (existing behavior) than to clamp to a wrong bound.
5. **Alpha (D3):** No `max_risk` in journal — geometry derivation from legs is the
   ONLY path. For Gamma/Beta trades that DO have `max_risk`, use it as a crosscheck
   against geometry-derived bound. If they disagree, use the more conservative (larger)
   bound and log the discrepancy.

**D4 coverage limitation (2026-05-20):** 56% of Alpha iron condors have impossible
geometry (credit > wing width). For those trades, the geometry clamp degrades to
no-op (no clamping). Commit 3's effective coverage for Alpha iron condors is
limited to the 44% with valid geometry. Diagonal spreads (same-strike, no wing
width concept) derive max loss from net debit and are fully covered when the
debit is known.

**Tests:** Unit tests for each spread type, geometry derivation, D4 degradation path,
crosscheck between max_risk and geometry-derived bound.
**Dependency:** See "Risk coupling with Commit 2" below.

---

### ⚠ Risk Coupling: Commits 2 and 3

Commit 2 (D2 unit fix) raises Alpha's effective stop threshold ~40×. Before
Commit 2, the unit mismatch causes stops to fire at ~2-5% of actual risk —
premature, but it accidentally limits loss booking to small amounts. After
Commit 2 corrects the threshold, Alpha trades can now lose up to the INTENDED
200% of credit before stopping — but `compute_trade_pnl()` is still unbounded
(D1). If IBKR paper sim produces impossible P&L between Commit 2 and Commit 3,
the correctly-thresholded stop will fire on the inflated P&L and book a loss
larger than structural max risk.

**In short:** Commit 2 alone WIDENS the D1 damage window on Alpha.

**OPEN DECISION:** Choose one:
- **(a) Bundle Commits 2+3** into a single session/commit — no window where
  the threshold is correct but P&L is unclamped. More complex, higher test
  burden, but eliminates the risk window entirely.
- **(b) Land 2 then 3 back-to-back with NO live trading window between** —
  ship both in the same session, pause position_monitor between the two
  commits (or ship during market-closed hours). Simpler per-commit but
  requires operational coordination.
- **(c) Accept the risk window** — if the probability of impossible P&L on
  Alpha positions is low enough (A020/A026 are the only open positions and
  may close naturally before Commit 2 ships), land them in separate sessions.

This decision depends on whether A020/A026 are still open when session S2 begins.

### Commit 4 — Phantom escalation (D6)
**Priority:** MEDIUM — prevents alert fatigue and cash lockup
**File:** `position_monitor.py` lines 1044-1063
**Change:** After N consecutive phantom alerts (N=3 → 3 hours):
1. Auto-mark journal entry as `PHANTOM_NEVER_FILLED`
2. Restore arm cash: `state["cash"] += max_risk`
3. Post single Discord alert: "🔴 Trade {id} auto-closed as phantom after {N}h"
4. Stop further alerts for this trade
**Tests:** Unit tests for phantom counter, auto-resolution, cash restoration.
**Dependency:** None — independent of Commits 2-3.

### Commit 5 — Entry fill confirmation gate (D5)
**Priority:** MEDIUM — prevents phantom creation at source
**File:** `gamma_agent.py` lines 1157-1162
**Change:** Two-phase journal write:
1. On submission: write journal with `status: "PENDING"` (not OPEN), do NOT
   decrement arm cash
2. On fill confirmation: update to `status: "OPEN"`, decrement cash
3. If order never fills (detected by position_monitor or by gamma_agent on
   next run): update to `status: "CANCELLED"`, no cash change
**Position_monitor change:** Skip PENDING trades in exit evaluation (they have
no broker position to evaluate).
**Tests:** Unit tests for PENDING→OPEN, PENDING→CANCELLED, position_monitor
skipping PENDING trades.
**Dependency:** Commit 4 (phantom escalation) should ship first as a safety net
for the transition period where some existing OPEN-but-phantom trades may still
exist.

---

## Operational Flags

### A026 (OXY diagonal) — LIVE, NO STOP — Decision: ACCEPT (a)
- **Status:** OPEN, 2/2 legs at broker, P&L = -$56.80 (stable across full Friday
  session, re-verified 2026-05-18 16:20-16:58 ET — not stale)
- **Risk:** Both `estimated_credit` and `net_debit` are zero in journal → the D2
  unit mismatch AND the zero-basis fallback both fail → no P&L-based exit fires.
  D1 unbounded-P&L bug is live on this position for ~2 trading days.
- **Natural close:** `expiry_proximity` fires when nearest leg expiry ≤ tomorrow.
  Short leg OXY260522C00055000 expires Thu May 22. Auto-close expected Wed May 21
  or Thu May 22.
- **Decision (2026-05-18):** Accept natural close (option a). Rationale: P&L stable
  -$56.80 across full Friday session, OXY catalyst calendar clean May 19-22
  (earnings behind May 5, ex-div June 10, OPEC met May 3, no conferences/rebalances).
- **Backstop: If A026 is not auto-closed by EOD Thu May 22, escalate to manual
  close. Do not let it persist into a third unmonitored day.**

### A020 (INTC iron condor) — LIVE, premature stop blocked by IBKR
- **Status:** OPEN, D2 unit mismatch fires stop at ~5% of risk, but IBKR has
  been refusing to fill close orders → effectively no exit is executing
- **Natural close:** Same expiry_proximity mechanism

### USB Phantom Trades (Gc001, Gd001)
- **Status:** OPEN in journal, 0 legs at broker, `PHANTOM_NEVER_FILLED` manual
  override pending
- **Resolution:** Will be auto-resolved by Commit 4 (phantom escalation), or
  can be manually resolved by editing journal before then

### 8 Dark Test Files
- **Issue:** 8 test files use `from conftest import ...` as module-level import,
  works on VPS but not from workstation. 104 tests only run via VPS pre-push hook.
- **Files:** Known set, pre-existing before any changes in this session.
- **Status:** Documented as known tech debt. Tests pass on VPS via pre-push hook.

---

## Session Calendar

**Depends on open decision re: Commits 2+3 coupling (see above).**

If bundled (option a):

| Session | Commits | Estimated effort | Prerequisite |
|---------|---------|-----------------|--------------|
| S2 | 2+3 (unit fix + geometry clamp) | ~3-4 hours | None |
| S3 | 4+5 (phantom + entry gate) | ~2 hours | Commits 2+3 shipped |
| S4 | Experiment restart | ~1 hour | All commits + phantoms resolved |

If back-to-back (option b) or separate (option c):

| Session | Commits | Estimated effort | Prerequisite |
|---------|---------|-----------------|--------------|
| S2 | 2 (unit fix) | ~1.5 hours | None |
| S2 cont. or S3 | 3 (geometry clamp) | ~2-3 hours | Commit 2 shipped, no trading window between |
| S3 or S4 | 4+5 (phantom + entry gate) | ~2 hours | Commit 3 shipped |
| S4 or S5 | Experiment restart | ~1 hour | All commits + phantoms resolved |

**Estimated total:** 3-4 sessions over ~1 week.

---

## Restart Prerequisites (all must be true)

- [x] Commit 1: Exit routing fix (22379bf)
- [ ] Commit 2: Alpha exit threshold fix
- [ ] Commit 3: Geometry-based P&L clamp
- [ ] Commit 4: Phantom escalation
- [ ] Commit 5: Entry fill confirmation gate
- [ ] USB phantom trades (Gc001/Gd001) resolved
- [ ] A020/A026 naturally closed or manually resolved
- [ ] All tests green (VPS pre-push hook)

## Abort Rule
If any step fails during restart: restore GAMMA_ENABLED=1, leave existing experiment
running. Half-applied restart is worst outcome.
