# Runbook: IBKR Gateway Port 4002 Refused

## Symptoms

- Discord alert: "🔴 IBKR Gateway port 4002 REFUSED"
- Dashboard `ibkr_port.status = "refused"` in `/var/dashboard/state/quantai-heartbeats.json`
- Error log: `IBKRBroker: gave up after 3 connect attempts to 127.0.0.1:4002 (last err: ConnectionRefusedError)`
- `collect_alpaca.py` writing `{"error": "ibkr broker connect failed"}` to `/var/dashboard/state/alpaca-account.json`
- Dashboard shows equity as `n/a` or stale

## Diagnosis

```bash
# 1. Is the gateway process alive?
systemctl is-active ibgateway        # should be "active" even when broken

# 2. Is the port actually bound?
python3 -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',4002)); print('OPEN' if r==0 else f'REFUSED errno={r}'); s.close()"

# 3. How long has it been down?
cat /tmp/quantai-heartbeats/ibkr_probe_fail.json

# 4. What do the IBC logs say?
ls -lt /root/ibc/logs/*.txt | head -3
# Then read the most recent (safe — no credentials in IBC logs):
tail -50 /root/ibc/logs/<most-recent-file>.txt
```

## Most common causes

1. **IBC login rate-limit after maintenance disconnect** — Process alive, port refused. Happens when IBKR pushes an unexpected mid-session disconnect and IBC retried too aggressively, triggering the 4-attempt lockout. *Symptom in IBC log:* `"Too many failed login attempts. Please wait N seconds before attempting to re-login again."`

2. **IBC startup race** — Gateway was just restarted (manually or by systemd); it's still initializing. Port 4002 typically becomes available 60–120 seconds after restart. **Wait 90s before taking action.**

3. **IBKR extended maintenance window** — IBKR sometimes extends their nightly maintenance past 00:15 ET. The `_is_in_restart_window()` guard in `_broker_ibkr.py` covers 23:30–00:15 ET. If the outage started within that window, wait until 00:30 ET before restarting.

4. **Gateway process crashed** — Both `systemctl is-active` reports "failed" AND port is refused. systemd will auto-restart (Restart=always, RestartSec=30). Check `journalctl -u ibgateway -n 30 --no-pager | grep -v -i 'pass\|pw='`.

## Fix

```bash
# Restart the gateway — safe at any time when no positions are open
sudo systemctl restart ibgateway

# Wait for JVM + IBC login to complete (~60-120s)
sleep 90

# Verify API port is bound and account is reachable
python3 -c "from ib_insync import IB; ib=IB(); ib.connect('127.0.0.1',4002,clientId=1); print(ib.isConnected(), ib.managedAccounts()); ib.disconnect()"
# Expected: True ['DUP851506']
```

After a successful restart, the circuit breaker in `collect_alpaca.py` resets on its next successful connection (within 5 minutes), and dashboard equity updates automatically.

## Prevention

- `ReconnectWindowSeconds=120` in `/opt/ibc/config.ini` — IBC waits 2 minutes between reconnect attempts, preventing the rapid retry storm that triggers IBKR's rate-limit.
- `LoginFailureAction=stop` in `/opt/ibc/config.ini` — IBC stops cleanly after a login failure rather than retrying. systemd handles the restart with a clean backoff.
- `heartbeat_monitor.py` port probe — detects port 4002 going down within 4 minutes and sends a Discord alert 24/7.
- `collect_alpaca.py` circuit breaker — after a failed connection, skips the next 5 minutes of retry attempts, suppressing the error flood.

## Related files

- `/opt/ibc/config.ini` — IBC reconnect configuration
- `/etc/systemd/system/ibgateway.service` — systemd unit (`Restart=always`, `RestartSec=30`)
- `/home/trader/QuantAI/v2/shared-data/scripts/heartbeat_monitor.py` — port probe + alert
- `/home/trader/QuantAI/v2/shared-data/scripts/_broker_ibkr.py` — `_is_in_restart_window()` guard (lines ~124–131)
- `/var/dashboard/collect_alpaca.py` — circuit breaker
- `/tmp/quantai-heartbeats/ibkr_probe_fail.json` — probe failure state (shows outage start time)
- `/root/ibc/logs/` — IBC login logs
