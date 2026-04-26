# Runbook: Centralized Error Logger

The single source of truth for every error/warning event on the VPS. Replaces the
text-log-scanning `error_detector.py` (now archived to `legacy/`).

## Architecture

| Component | Location |
|---|---|
| **DB** (source of truth) | `/var/dashboard/errors.db` (SQLite, WAL mode) |
| **Collector** (cron, every 2 min, root) | `/var/dashboard/collect_errors.py` |
| **Shared library** | `/var/dashboard/lib_errors.py` |
| **Python `logging` handler** | `/home/trader/QuantAI/v2/shared-data/scripts/_logger.py` |
| **Catalog** (canonical, JSON) | `/home/trader/QuantAI/docs/error-catalog.json` |
| **Dashboard state** | `/var/dashboard/state/quantai-errors.json` |
| **Dashboard tab** | `https://quantai.tail1465ff.ts.net/` → Errors |
| **Cron entry** | `*/2 * * * *  python3 /var/dashboard/collect_errors.py >> /root/logs/collect_errors.log 2>&1` |
| **Logrotate** | `/etc/logrotate.d/quantai-errors` (caps QuantAI text logs at 50 MB / daily / 7 retain) |

## Sources ingested

| Source family | Names | Cursor strategy |
|---|---|---|
| `systemd:*` | clawroute.service, litellm.service, openclaw.service | journalctl `__CURSOR` |
| `docker:*` | trader-orchestrator, trader-discord, trader-cto, trader-guards, litellm | RFC3339 timestamp |
| `textlog:*` | pipeline, heartbeat, position_monitor | byte offset + inode (rotation safe) |
| `syslog:*` | auth, kern, fail2ban, ufw | byte offset + inode + per-source filter regex |
| `pyapp:*` | heartbeat_monitor, position_monitor, autonomous_execution (currently) | inbox table drained per cycle |

To add a new Python script to the structured logging path:

```python
# At the top of the script:
import sys, logging
sys.path.insert(0, '/home/trader/QuantAI/v2/shared-data/scripts')
from _logger import setup as _logger_setup
_logger_setup('my_script_name')

# In your code:
logging.warning("something concerning happened: %s", details)
logging.error("real failure: %s", err, exc_info=True)
```

`print()` calls continue to be captured via the textlog ingestor (additive).

## Severity → Discord behavior

| Severity | Discord? | Throttle |
|---|---|---|
| `critical` | Yes, immediately | 5 min per `eid` |
| `error` | Yes (treated as warning for throttle) | 60 min per `eid` |
| `warning` | Yes | 60 min per `eid` |
| `info` | No (DB only, queryable) | — |

`eid` = matched `catalog_id` (e.g. `mleg-qty-missing`) or `unknown:<signature_hash>`.

## How to diagnose noise

Recent 5 most active eids (per source):
```bash
sudo sqlite3 /var/dashboard/errors.db "
SELECT source, catalog_id, COUNT(*) AS rows, SUM(count) AS occurrences
FROM events
WHERE last_seen >= datetime('now', '-24 hours')
GROUP BY source, catalog_id
ORDER BY occurrences DESC
LIMIT 5;"
```

Last 20 events from a specific source:
```bash
sudo sqlite3 /var/dashboard/errors.db "
SELECT first_seen, severity, count, substr(message, 1, 80)
FROM events
WHERE source = 'docker:trader-orchestrator'
  AND last_seen >= datetime('now', '-3 days')
ORDER BY last_seen DESC LIMIT 20;"
```

Throttled eids (already alerted recently):
```bash
sudo sqlite3 /var/dashboard/errors.db "
SELECT eid, last_alert_at, alert_count FROM throttle ORDER BY last_alert_at DESC LIMIT 10;"
```

## How to silence a noisy known pattern

1. Find the catalog entry: `grep -A4 '"id": "<eid>"' /home/trader/QuantAI/docs/error-catalog.json`
2. Set its `severity` to `info` and add a one-line `description` explaining the suppression.
3. The next collector cycle (≤2 min) re-reads the catalog. The pattern stops posting to Discord but keeps populating the DB.

## How to add a new source

To add a systemd unit:
- Append to `JOURNALCTL_UNITS` in `/var/dashboard/collect_errors.py`. Done.

To add a Docker container:
- Append to `DOCKER_CONTAINERS`.

To add a text log:
- Append to `TEXT_LOGS` as `(path, "textlog:<short_name>")`.

To add a syslog with custom filter:
- Append to `SYSLOGS` as `(name, path)`, then add a regex to `SYSLOG_FILTERS` at the top of the file.

## Reset / wipe

```bash
sudo rm /var/dashboard/errors.db /var/dashboard/errors.db-wal /var/dashboard/errors.db-shm
# Next collector run rebuilds schema and starts ingesting from "5 minutes ago"
sudo python3 /var/dashboard/collect_errors.py --verbose
```

To suppress historical-event Discord re-flood after a wipe-and-rebuild:
```bash
sudo python3 -c "
import sqlite3
from datetime import datetime, timezone
conn = sqlite3.connect('/var/dashboard/errors.db', isolation_level=None)
now = datetime.now(timezone.utc).isoformat()
for row in conn.execute('SELECT DISTINCT COALESCE(catalog_id, \"unknown:\" || signature_hash) FROM events WHERE severity IN (\"critical\",\"error\",\"warning\")'):
    conn.execute('INSERT INTO throttle (eid, last_alert_at, alert_count) VALUES (?, ?, 0) ON CONFLICT DO NOTHING', (row[0], now))
"
```

## Operating notes

- DB pruning: `prune_old()` deletes events with `last_seen < 30 days ago` and throttle rows older than 7 days. Runs on every collector cycle.
- Schema migrations: idempotent (`CREATE TABLE IF NOT EXISTS`). Adding columns later requires a one-shot `ALTER TABLE` because SQLite doesn't auto-migrate.
- Cross-process write: SQLite WAL + busy_timeout=5000ms. Python `_logger.SQLiteHandler` does single-row INSERTs, never blocks.
- Discord posting goes via `_discord.post_to_channel()` (uses `requests`, sends User-Agent, avoids Cloudflare 403).


---

## Resolution tracking (added 2026-04-26)

Every event row has two new columns: `resolved_at` (ISO timestamp, nullable) and
`resolved_by` (string tag, nullable). The dashboard's default view shows only
unresolved events; toggling to "All" reveals resolved rows greyed out.

### Three resolution paths

**1. Auto-resolve (the silent majority).** Every collector run, non-critical
events whose `last_seen` is older than `AUTO_RESOLVE_HOURS` (currently 2h) get
marked `resolved_by='auto'`. Critical events never auto-resolve — a real outage
shouldn't disappear from the dashboard just because it's old.

**2. Code-resolve (close the loop programmatically).** Scripts that fix an issue
can call `lib_errors.resolve()` to close out the corresponding error pattern.
Three helpers (all in `/var/dashboard/lib_errors.py`):

```python
from lib_errors import resolve, resolve_catalog, resolve_event

resolve(signature_hash, by="code")          # primary: by signature hash
resolve_catalog(catalog_id, by="code")      # by matched catalog entry id (most common)
resolve_event(event_id, by="manual")        # by row id (CLI uses this)
```

Example, in `position_monitor.py` after a successful close:
```python
sys.path.insert(0, "/var/dashboard")
from lib_errors import resolve_catalog
resolve_catalog("recurring-3c6683b1", by="code")
```

**3. Manual-resolve (Friday review sessions).** CLI at
`/var/dashboard/resolve_error.py`:

```bash
sudo python3 /var/dashboard/resolve_error.py --list-unresolved [--limit N]
sudo python3 /var/dashboard/resolve_error.py <event_id>             # by row id
sudo python3 /var/dashboard/resolve_error.py --signature-hash <h>   # by hash
sudo python3 /var/dashboard/resolve_error.py --catalog-id <id>      # by catalog id
sudo python3 /var/dashboard/resolve_error.py <id> --by review-2026-04-26   # custom tag
```

### Resurfacing rule

If a resolved event's signature appears again, the collector creates a **new
row** rather than reopening the old one. The dedup `WHERE` clause filters
`resolved_at IS NULL`, so resolved rows aren't candidates for incrementing.

This means: every "incident" gets its own row with its own first_seen/last_seen.
A flaky integration that breaks once a week shows up as one row per occurrence,
each independently resolvable, instead of one row with a count of 50 going back
6 months.

### Dashboard behavior

- **Unresolved view (default)**: severity badges + event list show only
  `resolved_at IS NULL`. The dashboard goes green when nothing is open.
- **All view**: same data plus the most recent 30 resolved events appended,
  greyed out (opacity-50) with a `✓ resolved · {by}` tag.
- **Critical-Open card**: appears only when there are unresolved critical
  events — fired criticals that auto-resolved 2h after fix don't show.

### Operating rules of thumb

- **Critical** stays open until you fix the issue and call `resolve_catalog()`,
  resolve via the CLI, or accept it'll stay there forever.
- **Error / Warning / Info** auto-close 2h after going quiet. If you want to
  close one immediately (e.g. you just merged a fix), call the CLI.
- A single fix that addresses multiple catalog entries: call `resolve_catalog`
  once per id, or pass a single signature hash if the entries share one.
- The 2h auto-resolve clock starts at `last_seen`, not `first_seen`. So a
  recurring noise that fires every 90 minutes never auto-resolves — that's
  intentional, it means the issue is still active.

### Pre-Monday cleanup procedure (one-time, 2026-04-26)

After this work landed, the following one-time bulk-resolve cleared historical
backlog:
```bash
sudo python3 /var/dashboard/resolve_error.py --catalog-id pipeline-silent-stale --by auto-bulk
sudo python3 /var/dashboard/resolve_error.py --catalog-id pipeline-stale-off-hours --by auto-bulk
sudo python3 /var/dashboard/resolve_error.py --catalog-id mleg-qty-missing --by auto-bulk          # fixed long ago
sudo python3 /var/dashboard/resolve_error.py --catalog-id anthropic-credit-balance-low --by auto-bulk  # pre-ClawRoute era
```

### Heartbeat noise fix (added 2026-04-26)

Catalog now distinguishes market-hours stale from off-hours stale via two
entries:
- `pipeline-silent-stale` (regex `pipeline beat=(stale|missing).*market=True`,
  severity `critical`) — real failure mode, pages immediately.
- `pipeline-stale-off-hours` (regex `pipeline beat=(stale|missing).*market=False`,
  severity `info`) — expected weekend/off-hours behavior, DB only, no Discord.

In tandem, `heartbeat_monitor.py` only logs the status line during off-hours when
the status changed or when ≥60 minutes have passed since the last log
(`OFF_HOURS_LOG_INTERVAL_MIN`). State tracked at
`/tmp/quantai-heartbeat-last-log.json`. Fail-open: any IO error → print anyway.
