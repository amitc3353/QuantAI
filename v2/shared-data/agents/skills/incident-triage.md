# Skill: Incident Triage

How to classify a finding into `safe_auto` / `propose_wait` / `never_touch`. The classification determines whether Sentinel applies the fix automatically, queues it for ✅ approval, or only observes.

## Decision tree

```
Is the target file in NEVER_MODIFY_PATHS or NEVER_TOUCH_PATHS?
  → never_touch (observed only; do not even propose)

Is the action a service restart?
  → If service in NEVER_RESTART_SERVICES_BLANKET (openclaw)?
      → never_touch
  → If service in POSITION_GATED_SERVICES (ibgateway)?
      → If market_hours == false AND open_positions == 0 AND ibkr_port check is 'error'?
          → safe_auto
      → Else:
          → propose_wait (operator approves with full context)
  → If service is a non-trading collector (collect_*.py)?
      → safe_auto

Does the action edit any code file?
  → propose_wait (humans review every code edit, even Sentinel's own files)

Is this a stale lock/temp file or known-noise SQL reclassification?
  → safe_auto (Sentinel runs catalog reclassification automatically; do not re-propose)

Is the error pattern not in the catalog?
  → propose_wait with a diff that adds a catalog entry — never silently invent severity

Confidence < 90%?
  → propose_wait

Otherwise: default propose_wait.
```

## Specific scenarios

### IBKR Gateway port refused

Most common failure mode. Three responses based on context:

| `market_hours` | `open_positions` | Response |
|---|---|---|
| true | any | `propose_wait` — describe restart command in card; operator decides |
| false | > 0 | `propose_wait` — even off-hours, positions in flight risk reconciliation issues |
| false | 0 | `safe_auto` if `ibkr_port` consecutive_fails ≥ 3, else `propose_wait` |

In all cases: include `runbook: docs/runbooks/runbook-ibkr-connection.md`.

If port refusal is during 23:30–00:15 ET and `ibkr_port` consecutive_fails is 1–2: this is the IBC nightly restart. Do not propose. Runbook: `runbook-ibkr-nightly-restart.md`.

### Stale collector cron

Collector hasn't updated its state file in N minutes (threshold from `infrastructure-health-metrics.md`).

| Collector | Response |
|---|---|
| `collect_alpaca.py`, `collect_quantai.py`, `collect_beta.py`, `collect_karna.py`, `collect_system.py`, `collect_history.py`, `collect_clawroute.py` | `safe_auto` restart |
| Anything writing to `quantai-positions.json` | `propose_wait` (positions are safety-critical input) |

Restart command: `pkill -f <collector>.py; python3 /var/dashboard/<collector>.py` — but **only if the collector is documented as idempotent**. If unsure, `propose_wait`.

### Catalog reclassification

Sentinel runs catalog reclassification automatically every apply cycle (`reclassify_catalog_noise()`). Do not propose this — it's a built-in safe_auto. The patterns covered are: `fail2ban.filter`, `[UFW BLOCK]`, `Invalid user`, `health-monitor: restarting (reason: stale-socket)`, SSH disconnect/connect-closed by invalid user, SSH authenticating-user noise.

If you spot a NEW pattern that's clearly noise but not in `RECLASSIFY_PATTERNS`: propose a `propose_wait` with a diff that adds it to the list. Do not auto-reclassify novel patterns.

### Novel error pattern not in catalog

Found a pattern in `errors.db` with `catalog_id IS NULL` and count > 5? Propose a `propose_wait` that adds a catalog entry. Include:
- A regex pattern that matches the signature
- Severity (default to `warning` for novel; never `critical` without runbook)
- A runbook stub at `docs/runbooks/runbook-<slug>.md` with diagnosis + fix sections

### Test failure

`quantai-test-results.json` has `failed > 0`. **Always `propose_wait`** — never auto-fix a test failure. The card description must include the failing test names. Runbook: empty (operator inspects pytest output).

### Disk pressure

`disk.used_pct ≥ 85`:
- Low-risk cleanup paths (`safe_auto`):
  - `/home/trader/QuantAI/v2/shared-data/scripts/auto_heal_data/digest_buffer/<date>.jsonl` for dates older than 7 days
  - `/root/quantai-v2/shared-data/logs/*.log.<N>` (rotated logs)
- Higher-risk (`propose_wait`):
  - Anything in `/root/quantai-v2/shared-data/journal/` (NEVER touch)
  - Anything in `auto_heal_data/applied/` (rollback receipts; user may need them)

### Weekend coverage

Saturday/Sunday observe runs at 10 AM ET. Most checks should be `info` or `ok` (no market). The relevant signals:
- `ibkr_port` should be `ok` (gateway should still be running)
- `weekly_synthesis` from Friday should exist
- `disk`, `memory` should be `ok`
- `errors.db` summary should not show a sudden spike

If anything's off-baseline, post to `#system-health` with a 🔴 prefix. No safe_auto restarts on weekends — operator approval only.

## Anti-patterns

- **Don't propose what Sentinel already does built-in.** Catalog reclassification is automatic; don't generate proposals for it.
- **Don't propose `safe_auto` for code edits.** Even one-line config changes — humans review.
- **Don't classify ibgateway-related fixes as `never_touch`.** They're `propose_wait` in most cases, `safe_auto` only under the strict three-condition check.
- **Don't suppress findings.** If a check is genuinely `error`, generate a finding even if you don't have a fix proposal. The operator needs visibility.
- **Don't generate a finding without a Discord-actionable summary.** The Discord card has 600 chars for description — make it count: what's wrong, what command would fix it, where to read more.
