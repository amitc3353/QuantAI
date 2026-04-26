# Legacy scripts

Files here are retired but preserved for reference.

## error_detector.py

Superseded by `/var/dashboard/collect_errors.py` on 2026-04-26.

The original detector scanned 4 QuantAI text logs only and wrote a JSON state file
plus `/tmp/quantai-error-dedup.json`. The new collector ingests from journalctl,
docker container logs, QuantAI text logs, /var/log/* (filtered), and a Python
SQLiteHandler inbox — into `/var/dashboard/errors.db` (the source of truth) plus
the same dashboard state file.

Cron entry removed at the same time.
