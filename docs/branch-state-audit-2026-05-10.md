# Branch-state audit — PR #4 vs main divergence

**Date:** 2026-05-11 (Sunday night UTC; pre-Monday 2026-05-11 market open)
**Trigger:** PR #4 (`fix/gamma-universe-expansion-2026-05-09`) shown as "open with conflicts" on GitHub. Concern that the 4-arm experiment was built assuming a merge that never happened.
**Scope:** Read-only audit. No remediation applied.
**PR URL:** https://github.com/amitc3353/QuantAI/pull/4

---

## 🎯 BOTTOM LINE

**The situation is benign.** PR #4's *content* is fully on `origin/main` — applied as different-SHA commits on 2026-05-09 — and has been the live code on the VPS ever since. The 4-arm experiment is **not** built on a phantom merge; it's built on commits that contain the exact same blob hashes the PR would have introduced.

What needs doing:
1. **Close PR #4 on GitHub** (manually, with a note that the work landed via different SHAs as commits `3ca7923`, `1d9adb0`, `67e0fa5`). No code action.
2. **Delete the stale branch** `fix/gamma-universe-expansion-2026-05-09` on origin (and the local tracking branch, if any) once the PR is closed.

That's it. No merge, no rebase, no conflict resolution, no production touch needed.

Section §5 below walks through *why* GitHub shows conflicts and §6 lists three resolution paths from safest to most explicit.

---

## 1. Where the code is actually running

### 1a. `origin/main` history (last 25)

```
2dbf8f6 docs(sentinel): pin promotion_history schema + G3 threshold caveat
a9bc6cd docs(sentinel): gamma 4-arm coverage plan — 11 new check specs
000b76e docs(roadmap): mark gamma 4-arm + universe expansion shipped; add live experiments section
fd37014 test(gamma): block .env auto-load in feature-flag tests
d8dbd8c feat(gamma): activate 4-arm A/B/C/D test — flag flip + sentinel freeze (commit 5 of 5)
1b9def3 feat(gamma): reporting + win-criteria evaluator + 4-arm dashboard tile (commit 4 of 5)
51c3f26 feat(gamma): per-arm broker routing + position monitoring + filter_setups_for_arm (commit 3 of 5)
4e6d0da feat(gamma): per-arm state tracking + journals (commit 2 of 5)
85f9fcb feat(gamma): 4-arm ranker abstraction + reward:risk estimator (commit 1 of 5)
33d5978 docs(gamma): 4-arm A/B/C/D ranking test plan
67e0fa5 feat(gamma): expand universe 27 → 155, add scanner F0 filter + parallelize    ← PR #4 content (different SHA)
1d9adb0 feat(gamma): spread verifier scaffolding (no behavior change yet)              ← PR #4 content (different SHA)
3ca7923 docs(gamma): diagnosis, expansion proposal, and implementation plan            ← PR #4 content (different SHA)
5002d01 docs: add ROADMAP.md tracker + session start checklist in CLAUDE.md            ← MERGE BASE
a9b47d7 chore: graphify post-commit rebuild (3621 nodes, 6401 edges)
87ebb80 fix: multi-symbol lesson retrieval — retrieve per candidate, not SPY-only
9590022 chore: graphify post-commit rebuild (3599 nodes, 6363 edges)
3e0bae1 feat: Phase 2 Item #1 — reflection memory + universal diagnostic
ec4b2df feat: add LLM call retry + parse hardening envelope (_llm_call.py)
76db958 feat: add centralized gate-block logger (gate_blocks.jsonl)
a6c35df merge: Phase 1 — 6 operational gates …
…
```

**All 6 4-arm commit SHAs verified as ancestors of `origin/main`:**
- `85f9fcb` ✓
- `4e6d0da` ✓
- `51c3f26` ✓
- `1b9def3` ✓
- `d8dbd8c` ✓
- `fd37014` ✓

### 1b. `origin/fix/gamma-universe-expansion-2026-05-09` (the PR branch)

Three commits, none in `origin/main`:

| SHA | Message | Authored |
|---|---|---|
| `b22aae7` | docs(gamma): diagnosis, expansion proposal, and implementation plan | 2026-05-09 22:21:27 UTC |
| `227bec3` | feat(gamma): spread verifier scaffolding (no behavior change yet) | 2026-05-09 22:21:56 UTC |
| `0ab04f1` | feat(gamma): expand universe 27 → 155, add scanner F0 filter + parallelize | 2026-05-09 22:29:57 UTC |

**These three commit messages match `3ca7923` / `1d9adb0` / `67e0fa5` on `origin/main` word-for-word.** Same author, same intent, different SHAs because they were re-applied (cherry-picked, rebased, or re-committed) onto main as fresh commits rather than via a `git merge`.

### 1c. Commits on PR branch but NOT in main

```
0ab04f1 feat(gamma): expand universe 27 → 155, add scanner F0 filter + parallelize
227bec3 feat(gamma): spread verifier scaffolding (no behavior change yet)
b22aae7 docs(gamma): diagnosis, expansion proposal, and implementation plan
```

### 1d. Commits on main but NOT in the PR branch (13 — the divergence)

```
2dbf8f6 docs(sentinel): pin promotion_history schema + G3 threshold caveat
a9bc6cd docs(sentinel): gamma 4-arm coverage plan — 11 new check specs
000b76e docs(roadmap): mark gamma 4-arm + universe expansion shipped
fd37014 test(gamma): block .env auto-load in feature-flag tests
d8dbd8c feat(gamma): activate 4-arm A/B/C/D test — flag flip + sentinel freeze (5/5)
1b9def3 feat(gamma): reporting + win-criteria evaluator + 4-arm dashboard tile (4/5)
51c3f26 feat(gamma): per-arm broker routing + position monitoring (3/5)
4e6d0da feat(gamma): per-arm state tracking + journals (2/5)
85f9fcb feat(gamma): 4-arm ranker abstraction + reward:risk estimator (1/5)
33d5978 docs(gamma): 4-arm A/B/C/D ranking test plan
67e0fa5 feat(gamma): expand universe 27 → 155, add scanner F0 filter + parallelize  [re-applied PR #4 content]
1d9adb0 feat(gamma): spread verifier scaffolding (no behavior change yet)            [re-applied PR #4 content]
3ca7923 docs(gamma): diagnosis, expansion proposal, and implementation plan          [re-applied PR #4 content]
```

### 1e. Merge base

```
5002d01 docs: add ROADMAP.md tracker + session start checklist in CLAUDE.md
        2026-05-09 20:59:49 UTC
```

This is the common ancestor of both refs. Both branches diverged from here.

---

## 2. What is live on the VPS

```
pwd:       /home/trader/QuantAI
branch:    main
HEAD:      2dbf8f63189fd521a6b3d4bad0932088889986c2  (matches origin/main exactly)
ahead/behind origin/main: 0 / 0
working tree: clean (only graphify-out autobuild + the new pre-week-scan doc)
```

- Working directory is on **`main`**, not on the PR branch.
- HEAD is the latest commit on origin/main.
- `is HEAD an ancestor of origin/fix/...?` → **NO**. Confirms we're not on a continuation of the PR branch.
- **Live universe size:** `len(UNIVERSE) == 155` ✓ (verified by importing `gamma.UNIVERSE` and by grep-counting ticker entries in `gamma/__init__.py`).
- Note: local branch `main` was created without `--track origin/main`, so `git rev-parse @{u}` complains. Functionally harmless because push/pull commands have been explicit (`git push origin main`).

---

## 3. State file

```
/root/quantai-v2/shared-data/cache/gamma_spread_status.json:
  verified_at:     2026-05-09T18:22:11-04:00   (1.2 days old — within freshness window)
  universe_size:   27    ← STALE: snapshot from the OLD 27-symbol universe
  results length:  27
  n_passed:        13
  n_blocked:       13
  n_fetch_failed:  1
```

**This is the pre-week-scan finding S1 surfacing again.** The state file is leftover from a manual `--verify-spreads` run that happened BEFORE the universe expansion landed on main. It doesn't prove PR #4 is unmerged — only that the verifier hasn't been re-run since the universe grew.

Filter F0 in `gamma/scanner.py` is fail-open for symbols missing from `spread_status` — so this doesn't break Monday's scan, but it does mean 128 of 155 symbols have no spread validation. (Documented in `docs/pre-week-scan-2026-05-10.md` as Findings C1 + S1.)

---

## 4. Conflict scope — the data behind the GitHub "conflicts" warning

### 4a. Full diff (PR branch ↔ main)

```
docs/gamma-four-arm-ab-test-plan.md                |  991 ---------------------
docs/sentinel-gamma-coverage-plan.md               |  414 ---------
v2/shared-data/plans/ROADMAP.md                    |   36 +-
v2/shared-data/scripts/gamma/arm_state.py          |  551 ------------
v2/shared-data/scripts/gamma/promotion_evaluator.py|  467 ----------
v2/shared-data/scripts/gamma/rankers/__init__.py   |   94 -
v2/shared-data/scripts/gamma/rankers/composite.py  |  104 -
v2/shared-data/scripts/gamma/rankers/reward_risk_first.py | 34 -
v2/shared-data/scripts/gamma/rankers/rsi_only.py   |   28 -
v2/shared-data/scripts/gamma/rankers/weighted_blend.py  | 58 -
v2/shared-data/scripts/gamma/reward_risk_estimator.py | 212 ---
v2/shared-data/scripts/gamma/risk_check.py         |  147 +-
v2/shared-data/scripts/gamma_agent.py              |  684 +--
v2/shared-data/scripts/gamma_weekly_digest.py      |  369 ---
v2/shared-data/scripts/position_monitor.py         |  107 -
v2/shared-data/scripts/sentinel_agent.py           |    5 -
v2/shared-data/tests/unit/test_gamma_ab_flag.py    |  221 -
v2/shared-data/tests/unit/test_gamma_arm_orchestration.py | 566 --
v2/shared-data/tests/unit/test_gamma_arm_state.py  |  576 --
v2/shared-data/tests/unit/test_gamma_journal_compat.py | 310 -
v2/shared-data/tests/unit/test_gamma_promotion_logic.py | 587 -
v2/shared-data/tests/unit/test_gamma_rankers.py    |  630 -
22 files changed, 12 insertions(+), 7179 deletions(-)
```

Read direction: `main ← PR branch` = "what the PR branch needs to *remove* (relative to main) to reach equality". Almost everything is a deletion because main has 9 commits of work the PR branch doesn't have.

### 4b. Real merge-tree probe

```bash
git merge-tree --write-tree origin/main origin/fix/gamma-universe-expansion-2026-05-09
```

Output:
```
3b5afe350195229efa007db92232cdac7bb370de
100644 60cf9d151e7ab65451ef1a5233032787ac3afc75 1   v2/shared-data/scripts/gamma_agent.py
100644 4878540073c931ff88513d5ed6913148cf7e3f17 2   v2/shared-data/scripts/gamma_agent.py
100644 b96f4c08f00d217130de93ed8849d5db9920a089 3   v2/shared-data/scripts/gamma_agent.py

Auto-merging v2/shared-data/scripts/gamma_agent.py
CONFLICT (content): Merge conflict in v2/shared-data/scripts/gamma_agent.py
```

**The single conflict file is `gamma_agent.py`.** Stage 1/2/3 indicate the three-way merge couldn't auto-resolve.

### 4c. Why the conflict exists

| Side | What it did from merge base | Lines |
|---|---|---|
| Merge base (`5002d01`) | Pre-universe-expansion `gamma_agent.py` | ~360 |
| `origin/main`'s 13 commits | Added: F0 filter, `run_verify_spreads`, all 4-arm dispatch (run_scan_4arm, run_execute_4arm, --promote-arm, --reset-experiment, --evaluate-promotion, .env autoload, etc.) | **1344 lines** |
| `origin/fix/...`'s 3 commits | Added: F0 filter, `run_verify_spreads`, scan_with_indicators integration | **668 lines** |

Both touched the same regions of `gamma_agent.py` to add the F0/verify-spreads hooks. **The PR-branch additions are a STRICT SUBSET of what main has.** Git can't tell that semantically, so it reports a conflict.

### 4d. Conflict resolution would be: "keep main"

Every line the PR branch wants to add already exists in `origin/main`:

| Symbol | main | PR branch |
|---|---|---|
| `def run_verify_spreads` | 1 occurrence | 1 occurrence |
| `SPREAD_STATUS_PATH` | 4 occurrences | (yes) |
| `VERIFY_SPREADS` flag handling | 9 occurrences | (yes) |
| `SPREAD_CHECK_ENABLED` env var | (yes) | (yes) |
| Universe size 155 | ✓ | ✓ |
| F0 filter in scanner | ✓ | ✓ |
| `gamma_spread_status.json` writer | ✓ | ✓ |

**All three universe-expansion source files are byte-identical between main and PR branch:**

| File | main blob | PR branch blob | Status |
|---|---|---|---|
| `v2/shared-data/scripts/gamma/__init__.py` | `ce3a25da...` | `ce3a25da...` | ✓ identical |
| `v2/shared-data/scripts/gamma/scanner.py` | `ee352fc4...` | `ee352fc4...` | ✓ identical |
| `v2/shared-data/scripts/gamma/spread_verifier.py` | `76a1f919...` | `76a1f919...` | ✓ identical |

Only `gamma_agent.py` differs (main has 676 additional lines of 4-arm + sentinel + reporting work the PR branch never saw).

---

## 5. Diagnosis — why the PR shows "open with conflicts"

Reconstructed sequence:

1. **2026-05-09 ~22:00 UTC** — branch `fix/gamma-universe-expansion-2026-05-09` created from `5002d01` (then-tip of main).
2. **2026-05-09 22:21–22:29 UTC** — three commits pushed to the branch (`b22aae7`, `227bec3`, `0ab04f1`).
3. **2026-05-09 (slightly later)** — instead of merging the PR through GitHub's UI (or `git merge --no-ff` locally), the same content was applied directly to `main` as three fresh commits with different SHAs (`3ca7923`, `1d9adb0`, `67e0fa5`). This was likely a cherry-pick / `git format-patch | git am` / local-rebase-and-push, or three new commits authored by hand against the live working tree. The result is content-equivalent but ancestrally disjoint.
4. **2026-05-09 → 2026-05-11** — 9 more commits stacked on main (4-arm test plan + 5 implementation commits + 1 test fix + 3 docs). All built on the re-applied content. None on the PR branch.
5. **GitHub** still sees PR #4 as "open" because the PR's commit SHAs (`b22aae7`, `227bec3`, `0ab04f1`) are not in `origin/main`'s ancestry. Its auto-merge probe sees a real conflict in `gamma_agent.py` (because main extended that file far beyond what the PR branch did) and reports "conflicts."

**This is a process artifact, not a code problem.** The work landed; the PR just wasn't closed.

---

## 6. Resolution paths (do NOT execute yet — listed for your decision)

### Option A — Close PR #4 manually (RECOMMENDED, safest)

- Go to https://github.com/amitc3353/QuantAI/pull/4
- Comment: *"Content was applied to main as commits `3ca7923` (docs), `1d9adb0` (verifier scaffolding), `67e0fa5` (expansion + scanner F0). Closing as already-merged via different SHAs. Same content, different commit history. PR was not closed at the time due to a process slip."*
- Click "Close pull request" (NOT "Merge")
- Delete branch `fix/gamma-universe-expansion-2026-05-09` on origin
- Optionally delete the local tracking ref: `git branch -D fix/gamma-universe-expansion-2026-05-09; git push origin --delete fix/gamma-universe-expansion-2026-05-09`

**Why this is best**: zero risk of corrupting history. No re-introduction of duplicate content. No empty merge commit. The PR description and review comments are preserved on GitHub.

### Option B — `git merge --strategy=ours` from main

```bash
git checkout main
git pull origin main
git merge -s ours origin/fix/gamma-universe-expansion-2026-05-09 \
  -m "merge: close PR #4 — content already on main as 3ca7923/1d9adb0/67e0fa5"
git push origin main
```

Creates an empty merge commit that records "we considered this branch and chose main's version." GitHub will then automatically detect the PR as merged and close it. Branch can be deleted afterward.

**Trade-off**: adds one merge commit to main's history. Pre-push hook will re-run the test suite (which passes 1433/1433). Otherwise safe.

### Option C — Force-close via state-only sync (NOT RECOMMENDED)

Reset the PR branch to main's tip and push:
```bash
git checkout fix/gamma-universe-expansion-2026-05-09
git reset --hard origin/main
git push origin fix/gamma-universe-expansion-2026-05-09 --force
```
GitHub will then say "0 commits ahead of main" and the PR can be closed without a merge.

**Why NOT this**: requires force-push to a feature branch. Loses the original PR-branch commit history. Confusing for anyone reviewing the closed PR later. Possible if you have an audit-trail reason to keep the PR open and just sync — but Option A achieves the same with less mechanism.

---

## 7. What is NOT a problem (clarifications)

| Concern | Status |
|---|---|
| "The 4-arm experiment was built on a phantom merge" | **Refuted.** It was built on commits `3ca7923`/`1d9adb0`/`67e0fa5` which contain the byte-identical content the PR would have introduced. |
| "Live universe is 27, not 155" | **Refuted.** `len(UNIVERSE) == 155` in the live code. The 27 in `gamma_spread_status.json` is a stale state-file snapshot from before the expansion ran. |
| "GAMMA_AB_TEST_ENABLED is wrong because PR isn't merged" | **Refuted.** Flag is `=1` in `.env`; collector + agent both read it correctly; experiment_active=True in dashboard state. |
| "Need to resolve conflicts before Monday's scan" | **Refuted.** Conflicts only exist on the PR branch view. Main is internally consistent. The scheduled `gamma_agent.py --scan` cron at Monday 16:30 ET will run against main's current `gamma_agent.py`, which has all the universe expansion + 4-arm dispatch logic. |

---

## 8. What this audit confirms is fine

- Working tree at `2dbf8f6` (latest main)
- All 4-arm commit SHAs are ancestors of origin/main
- All universe-expansion source files (`__init__.py`, `scanner.py`, `spread_verifier.py`) have byte-identical blob SHAs between main and PR branch
- Pre-push hook test gate is green (1433 passing)
- Production cron points at the live files on disk; nothing references the PR branch SHAs
- No risk to Monday's trading from this branching artifact

---

## Recommendation summary

1. **Take no code action.** The system is internally consistent.
2. **Close PR #4 on GitHub manually** (Option A) with a comment pointing to the equivalent main-side SHAs. Delete the stale branch.
3. **Optional follow-up** unrelated to this PR but surfaced by the audit: add `branch.main.remote = origin` + `branch.main.merge = refs/heads/main` to the local git config so `@{u}` works (currently warns about "no upstream"). Cosmetic.
4. **Carry through the pre-week-scan recommendations** (`docs/pre-week-scan-2026-05-10.md`) — those findings about the missing `--verify-spreads` cron and the stale 27-symbol `gamma_spread_status.json` are still valid and unrelated to this PR situation.

---

**Audit complete. No commits, no merges, no force-pushes, no branch deletions performed. Awaiting your decision on Option A / B / C.**
