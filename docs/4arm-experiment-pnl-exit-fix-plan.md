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

**Commit 1 (exit routing)** shipped 2026-05-18. Commit 2 was **intentionally skipped
per the Path 3 decision (2026-05-20)** — see "Path Decision" section below.
**Commit 3 absorbs the exit threshold responsibility via geometry-based P&L
clamping**, and the remaining fixes (Commits 4 and 5) address phantom escalation
and entry-fill confirmation. The clamp design handles paper-sim impossible fills
(D4 majority case) by deliberately ignoring credit in the wing-based bound — see
Commit 3 design for the explicit overstatement trade-off.

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

### Commit 2 — Alpha exit threshold fix (D2) — ⊘ NOT IMPLEMENTED (intentionally skipped)

**Verification completed 2026-05-20:**
- ×100 hypothesis **confirmed** for iron condors (per-share → total-dollar)
- ×100 hypothesis **disproved** for diagonals (already total-dollar, ×100 would
  set stop at 100× max loss, effectively disabling it)
- bull_put_spread, bear_call_spread: **design-time only, zero journal entries,
  no Commit 2 coverage needed**
- A026 (diagonal, credit=0): LLM omission, not convention — no fix to
  estimated_credit produces a working stop for this trade

### Path Decision (2026-05-20)

**Selected: Path 3 — skip Commit 2 entirely, proceed to Commit 3.**

Commit 2 is intentionally skipped. The unit mismatch in position_monitor.py
lines 848-863 is NOT fixed as a standalone change. Commit 3 (geometry-based
P&L clamp) absorbs the exit threshold responsibility.

**Reasoning:**
1. **A026 is unprotected under ALL paths.** estimated_credit=0 and net_debit
   absent mean no fix to the credit-based threshold produces a working stop.
   A026 rides accept(a) backstop to natural close Wed/Thu May 22 regardless
   of which path is chosen. This collapses Commit 2's primary urgency argument.
2. **A020 is broker-frozen.** IBKR refuses to fill close orders. Whether the
   stop fires at 5% (current mismatch) or 200% (fixed), the result is the
   same: close order submitted, IBKR rejects it. Fixing the threshold is
   theoretically correct but operationally inert for this position.
3. **Alpha is paused.** ALPHA_ENABLED=0. No new Alpha trades enter. The only
   affected positions (A020, A026) are approaching natural close this week.
4. **Path 1 (standalone fix) creates throwaway code.** Strategy-branching
   logic in position_monitor.py would likely be superseded by Commit 3's
   geometry approach — writing, testing, reviewing, and then removing code
   is negative-value work.
5. **D4 majority (56% of iron condors)** changes the strategic picture.
   Commit 3's D4 degradation strategy is the real design challenge, not
   the unit mismatch. Engineering time is better spent there.

**Risk accepted:** The unit mismatch persists in position_monitor.py lines
848-863 until Commit 3 lands. If Commit 3 slips, the bug remains in an
active code path.

**Mitigation gate: Re-enabling Alpha (ALPHA_ENABLED=1) is explicitly gated
on Commit 3 landing.** Alpha must not be re-enabled while the unit mismatch
is live. This gate must be operationalized (see below).

**Commit numbering preserved:** Commit 3 stays "Commit 3" for traceability
across plan documents and conversation history. There is no Commit 2.

### Commit 3 — Geometry-based P&L clamp (D1 + D3 + D4)
**Priority:** HIGH — protects all agents from impossible P&L booking
**File:** `position_monitor.py` — new function `derive_max_loss()` + clamp integration
**Decision (2026-05-20):** Option A for exit thresholds — leave lines 848-863 as-is.
Clamp caps P&L input; existing thresholds run on clamped value. Threshold fix deferred
to Alpha redesign.

**Design — 3-tier clamp using wing_width, ignoring credit:**

```
compute_trade_pnl(trade, alpaca_pos) → raw P&L
  ↓
derive_max_loss(trade) → Optional[float]
  Tier 1: max_risk in journal?      → return max_risk          (Beta, Gamma)
  Tier 2: wing_width from geometry? → return wing_width × 100  (Alpha iron condors, verticals)
  Tier 3: neither available?        → return None               (Alpha diagonals, single-leg)
  ↓
if derived is not None:
    pnl = max(pnl, -derived)   # clamp floor
else:
    log warning, pass through unclamped
```

**Key design choice: wing_width alone, credit ignored.**
- Sidesteps D4 entirely — wing_width is always positive, no degradation path needed
- 100% of iron condors get a clamp (including the 56% with D4 impossible fills)
- Trade-off: clamp is coarser than true max loss by the credit amount
  - $1-wide, $0.75 credit: true max $25, clamp $100 (4× overstatement)
  - $2.50-wide, $0.65 credit: true max $185, clamp $250 (1.35× overstatement)
  - Still dramatically better than unbounded (current: -248% observed)

**Tier 1 crosscheck:** When `max_risk` AND wing_width are both available, compare.
If `max_risk > wing_width × 100`, log discrepancy (suggests data issue). Use
`max_risk` as clamp regardless (canonical field from entry agent).

**Tier 3 (no clamp):** Same-strike diagonals (Alpha A026-class), single legs,
missing/empty legs. These trades get NO clamp. Logged with trade ID for
operator visibility. Acceptable because:
- Alpha diagonals are rare (9/25 Alpha entries, all closed except A026)
- A026 is riding accept(a) backstop to natural close
- Future Alpha trades are gated on Commit 3 + Alpha redesign

**Exit thresholds (lines 848-863): UNCHANGED.** The buggy estimated_credit-based
thresholds persist. The clamp makes booked P&L safe (bounded) even when premature
stops fire. Alpha re-enablement is safe (no unbounded P&L) but not correct
(premature stops). Threshold correctness deferred to Alpha redesign with proper
journal field normalization.

**Tests:** 14 cases covering all tiers, D4, crosscheck, edge cases (see plan).
**Single commit.** ~200-250 lines (function + integration + tests).
**Estimate:** ~2-3 hours implementation + testing.

---

### ⚠ Risk Coupling: Commits 2 and 3 — RESOLVED

**Decision (2026-05-20):** Risk coupling is moot. Commit 2 was skipped (Path 3).
The unit mismatch in position_monitor.py lines 848-863 persists at its current
(premature-stop) behavior until Commit 3 replaces or supersedes the
estimated_credit-based exit logic. No window exists where thresholds are correct
but P&L is uncapped, because the threshold is never corrected independently.

**Residual risk:** The buggy estimated_credit thresholds remain live. For Alpha
trades, this means premature stops at ~2-5% of actual risk — but IBKR refuses
to fill the resulting close orders, making the bug operationally inert for
current positions. Alpha re-enablement is gated on Commit 3.

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

### KARNA Operational Issues (2026-05-29)

Captured from Discord error report posted by KARNA at 2026-05-29 02:00 UTC.
**These are KARNA-infrastructure issues, separate from the 4-arm QuantAI fix
sequence.** Queued here for follow-up.

#### Issue 1: False-positive backup failure alert
- **Discord said:** "KARNA Daily Backup FAILED — exec blocked by allowlist policy"
- **Reality:** The backup actually succeeded. `/root/logs/backup.log` shows
  `[Fri May 29 02:00:04 AM UTC 2026] Backup pushed: karna-backup-2026-05-29.tar.gz.age | files=48 | verified=OK`
  pushed to `github.com/amitc3353/karna-backups`. **No data loss.**
- **Root cause:** Two processes try to do the backup at the same time:
  1. System cron (`0 2 * * * /root/scripts/karna-backup.sh`) — runs as root,
     no allowlist applies, succeeds in ~3 seconds
  2. KARNA's own scheduled routine — tries to exec the same script through
     its sandboxed shell, hits the allowlist deny, reports "FAILED" to Discord
- **Recommended fix (any of):**
  - **(a)** Move KARNA's routine from "execute the backup" to "verify the backup
    ran by tailing `/root/logs/backup.log` and checking the most recent
    `Backup pushed` line within the last 2 hours". Adds `/root/logs/backup.log`
    to the read-only allowlist. KARNA reports OK/STALE based on log content.
    Cleanest separation: cron does the work, KARNA does the verification.
  - **(b)** Move `/root/scripts/karna-backup.sh` to `/home/openclaw/scripts/`
    and add the new path to KARNA's exec allowlist. Replaces the system cron
    with a KARNA-driven cron. More moving parts; reintroduces dependency on
    KARNA being healthy at 02:00 UTC.
  - **(c)** Disable KARNA's daily backup routine entirely and rely on the
    system cron alone. Trade-off: loses Discord visibility into successful
    backups.
- **Recommendation:** Option (a). Keeps the system cron as the producer
  (already proven reliable), adds KARNA as a read-only verifier with
  Discord visibility.
- **Priority:** Low — the actual backup is succeeding. The Discord alert is
  noise. No urgency unless the operator wants the false alarms silenced.

#### Issue 2: Gemini embedding API key invalid → memory search broken
- **Symptom:** KARNA reports "Memory search unavailable — Gemini embedding API
  key invalid" during the same 02:00 UTC routine.
- **Impact:** KARNA cannot semantically search prior memory entries. Functional
  effect depends on how often memory search is used by routines. Likely
  degrades context-aware responses but doesn't break trading or backups.
- **Recommended fix:** Rotate the Gemini API key in OpenClaw config. If the
  key is rate-limited rather than invalid, switch to a different embedding
  provider (OpenAI text-embedding-3-small is a common fallback, or a local
  model via litellm if cost is a concern).
- **Priority:** Low-to-medium — depends on how much KARNA's intelligence
  depends on memory search. Worth checking what fraction of KARNA's recent
  responses are degraded by this.

#### Investigation notes
- Verified backup success at 02:00:04 UTC via `/root/logs/backup.log` (sudo read).
- `/root/scripts/karna-backup.sh` and `/home/openclaw/scripts/` are
  permission-denied from the workstation account — investigation requires
  root or operator review of OpenClaw's allowlist config.
- The KARNA-side fix (changing what its routine does, or updating its API key)
  lives outside the QuantAI repo. This entry is a placeholder for the operator
  to pick up in a separate work stream.

### Plan-file divergence (2026-05-20)
- **What happened:** This plan lives in two locations: `.claude/plans/` (plan-mode
  working copy) and `docs/4arm-experiment-pnl-exit-fix-plan.md` (committed, git-tracked).
  During the 2026-05-20 session, plan-mode edits (Path 3 decision, Option A, Commit 3
  redesign) were applied to `.claude/plans/` but not synced to `docs/` until this commit.
  The files diverged for the duration of the session.
- **This commit consolidates them.** After this commit, both files are identical.
- **Structural fix needed:** Before the next plan-mode edit session, establish a single
  source of truth — either symlink `docs/` → `.claude/plans/`, auto-sync at session end,
  or stop using `.claude/plans/` and edit `docs/` directly. The current two-copy pattern
  will diverge again.

---

## Session Calendar

**Commit 2 skipped (Path 3). Commit 3 is the next implementation work.**

| Session | Commits | Estimated effort | Prerequisite |
|---------|---------|-----------------|--------------|
| S2 (current) | 3 (geometry clamp + exit threshold) | ~3-4 hours | Plan approval |
| S3 | 4+5 (phantom + entry gate) | ~2 hours | Commit 3 shipped |
| S4 | Experiment restart | ~1 hour | All commits + phantoms resolved |

**Estimated total:** 3 sessions remaining, ~1 week.

---

## Restart Prerequisites (all must be true)

- [x] Commit 1: Exit routing fix (22379bf)
- [⊘] Commit 2: Skipped (Path 3, 2026-05-20) — unit mismatch absorbed by Commit 3
- [ ] Commit 3: Geometry-based P&L clamp + exit threshold replacement
- [ ] Commit 4: Phantom escalation
- [ ] Commit 5: Entry fill confirmation gate
- [ ] USB phantom trades (Gc001/Gd001) resolved
- [ ] A020/A026 naturally closed or manually resolved
- [ ] All tests green (VPS pre-push hook)

## Abort Rule
If any step fails during restart: restore GAMMA_ENABLED=1, leave existing experiment
running. Half-applied restart is worst outcome.
