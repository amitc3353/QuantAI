# Runbook: pipeline silent fail

## Detection
- Pattern: `pipeline beat=stale` in heartbeat.log during market hours
- Symptom: `heartbeat_monitor.py` posts Discord alert "stale pipeline beat age=Xm market=True". Pipeline stopped firing mid-session.

## Diagnosis
1. Check `sudo crontab -l` — are UTC hours correct for current DST? EDT = `13-20`, EST = `14-21`. Mismatch silently breaks the schedule.
2. `sudo tail -100 /root/quantai-v2/shared-data/logs/pipeline.log` — look for uncaught exception or the last timestamp.
3. `sudo tail /var/log/syslog | grep CRON` — did cron actually fire the pipeline? If not, the issue is the cron entry, not the script.
4. `ls -l /tmp/quantai-heartbeats/pipeline.beat` — mtime tells you when `run_pipeline.py` last finished a market-hours cycle.

## Fix
- **Cron wrong hours (DST):** update root crontab UTC hours ±1.
- **Exception crashed pipeline:** fix the underlying bug in the failing script, then wait for next cycle.
- **Cron disabled:** `sudo systemctl status cron` — restart if dead.

## Auto-fixable?
**No.** Root cause varies too widely (code, schedule, infra). Surface to Discord, human diagnoses.

## Prevention
- Heartbeat monitor catches silent failures within 2-22 min (20-min threshold + 2-min cadence).
- DST reminder scheduled for 2026-11-01 via `at`.
- Pipeline writes heartbeat at every market-hours exit path in `run_pipeline.py`.
