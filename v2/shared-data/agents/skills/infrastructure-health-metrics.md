# Skill: Infrastructure Health Metrics

How to interpret `system-health-report.json` and decide when a check's status warrants action.

## Status hierarchy (severity order)

| Status | Meaning | Sentinel response |
|---|---|---|
| `ok` | Within normal range | None |
| `info` | Notable but not actionable (e.g. one transient port-probe blip) | None |
| `warning` | Sustained anomaly; may self-resolve | Observe; queue if persists 2+ cycles |
| `error` | Active failure; user-visible if not addressed | Propose action this cycle |

Top-level report `status` = highest severity across all 13 checks.

## Per-check thresholds

### `ibkr_port`, `litellm_4000`, `clawroute_18790` (port probes)

The `_port_check()` primitive maintains a consecutive-fail counter that survives process restarts (`/tmp/quantai-heartbeats/<probe>_fail.json`).

| `consecutive_fails` | Status | Why |
|---|---|---|
| 0 | `ok` | Healthy |
| 1 | `info` | One blip — could be transient (network, GC pause, restart window) |
| 2 | `warning` | Real — 4+ minutes of port refusal |
| ≥3 | `error` | 6+ minutes; broker is down. Propose restart only if safety gates allow. |

For `ibkr_port`, also consult `IBKRBroker: in IB Gateway restart window` log entries — port refusal during 23:30–00:15 ET is the IBC nightly restart and is expected. Runbook: `docs/runbooks/runbook-ibkr-nightly-restart.md`.

### `disk` and `memory`

| Used % | Status |
|---|---|
| < 85 | `ok` |
| 85–91 | `warning` |
| ≥ 92 | `error` |

Disk recovery: rotate logs, archive old `auto_heal_data/applied/` receipts, drain `digest_buffer/` for past dates. Memory recovery: usually transient; restart the offending Python process.

### `cron_freshness`

A cron is "stale" if its last run is older than 2× its expected interval. The `cron-status.json` collector flags this. Sentinel's response depends on market hours:
- Stale during 9:30–16:00 ET weekday: `error`
- Stale off-hours: `warning` (cron may simply not have a run scheduled)

### `self_learning_sla`

For every CLOSED journal entry in the last 24h, both `capability_requests/<agent>/<trade_id>.json` AND `trade_reviews/<agent>/<trade_id>.md` must exist within 10 minutes of close. Missing = `error`. Common cause: `agent_self_diagnosis.py` or `trade_reviewer.py` cron not firing or failing silently.

### `weekly_synthesis`

Active only Friday after 22:00 UTC, or any time Sat/Sun. The latest `weekly_reports/*.md` file must be ≤ 8 days old. Older = `error`. Common cause: `weekly_synthesis.py` failed; check `/root/quantai-v2/shared-data/logs/` for traceback.

### `collector_staleness`

Every `/var/dashboard/state/*.json` (except `COLLECTOR_SKIP_FILES`) must have `last_updated` within:
- 10 minutes during market hours (`error` if exceeded)
- 60 minutes off-hours (`warning` if exceeded — some collectors only update on change)

### `journal_schema`

Last `trades.jsonl` line must parse as JSON and contain `status` + `trade_id|id` + `legs`. Missing fields = `warning`. Parse failure = `error`.

### `test_results`

`/var/dashboard/state/quantai-test-results.json` must exist with `failed=0`, age < 24h. `failed > 0` = `error`. Stale = `warning`.

### `graphify`

`graphify-out/graph.json` mtime ≤ 7 days. Older = `info` (graph drift; not blocking; monthly `/graphify --update` routine handles this).

### `open_positions`

Informational only. Sentinel reads this for safety gates (ibgateway restart blocked when count > 0).

## Decision rule

If `system_health.status == "ok"` AND `errors_db.by_severity` has 0 critical/error events: **return empty findings and proposals**. Silence is correct.

If a check is `warning` for the first cycle: log to digest but do not act. If still `warning` 2+ cycles later: propose action.

If a check is `error`: propose action this cycle (subject to validation rails).
