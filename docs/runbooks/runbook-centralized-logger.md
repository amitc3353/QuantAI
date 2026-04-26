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
