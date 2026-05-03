# Runbook: IBKR Nightly Restart — Transient Connection Errors

## Symptoms

- Discord alert (if threshold crossed): IBKR `Error 1100: Connectivity between IBKR and Trader Workstation has been lost`
- Dashboard `ibkr_port.status` briefly shows `refused` around 23:45–00:15 ET
- Pipeline logs: `IBKRBroker: in IB Gateway restart window (23:30–00:15 ET) — refusing to connect`
- Pipeline logs: `client id is already in use` during post-restart reconnect race
- Pipeline logs: `positions request timed out` or `API connection failed: TimeoutError`

These patterns are **expected** during the IBC-managed nightly gateway restart at 23:45 ET
and the subsequent reconnect window (typically 30–90 seconds).

## Why this happens

IBC (`/opt/ibc/config.ini`) is configured with `AutoRestartTime=23:45`. At that time, IBC
logs out of IBKR, waits for in-flight requests to drain, then reinitialises the session.
During the restart window:

1. Port 4002 stops accepting connections → Error 1100 fires on any active `ib_insync` session
2. `heartbeat_monitor.py` may record `consecutive_fails` briefly before port reopens
3. If a cron job runs in the window, it sees `ConnectionRefusedError` → circuit breaker trips
4. After port reopens, the first reconnect attempt may see `clientId already in use` if TWS
   hasn't fully cleared the session yet

`ReconnectWindowSeconds=120` (added 2026-05-03) means IBC waits 2 minutes between retry
attempts, which avoids the login rate-limit that caused the 2026-05-02 outage.

## Diagnosis

```bash
# Is the gateway in the restart window right now?
python3 -c "
from zoneinfo import ZoneInfo; from datetime import datetime
now = datetime.now(ZoneInfo('America/New_York'))
print(now.strftime('%H:%M ET'), 'restart window = 23:30–00:15')
"

# Check IBC config
grep -E "AutoRestartTime|ReconnectWindowSeconds|LoginFailureAction" /opt/ibc/config.ini

# Verify port status after window should have closed
python3 -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('127.0.0.1',4002)); print('OPEN' if r==0 else f'REFUSED errno={r}'); s.close()"

# Check ibkr_probe_fail state (consecutive fail count)
cat /tmp/quantai-heartbeats/ibkr_probe_fail.json
```

## Normal vs. Abnormal

| Scenario | Duration | Action |
|----------|----------|--------|
| Port refuses during 23:30–00:15 ET | ~30–90s | None — expected restart window |
| Error 1100 during restart window | momentary | None — gateway is restarting |
| `clientId already in use` on first reconnect | 1–2 retries | None — clears automatically |
| `positions request timed out` in window | single occurrence | None |
| Port still refused at 00:20 ET | >35 min | Follow `runbook-ibkr-connection.md` |
| `consecutive_fails >= 2` outside 23:30–00:15 | anytime | Follow `runbook-ibkr-connection.md` |

## Fix (if needed outside window)

If the port is still refused after 00:15 ET, escalate to the main IBKR connection runbook:

```bash
sudo systemctl restart ibgateway
sleep 90
python3 -c "from ib_insync import IB; ib=IB(); ib.connect('127.0.0.1',4002,clientId=1); print(ib.isConnected(), ib.managedAccounts()); ib.disconnect()"
# Expected: True ['DUP851506']
```

## Prevention

- `ReconnectWindowSeconds=120` in `/opt/ibc/config.ini` prevents retry storm after restart
- `heartbeat_monitor.py` `ok-overnight` demotion: stale beat <18h off-hours → no alert
- Circuit breaker in `collect_alpaca.py` stops hammering the port during window (5-min backoff)
- `_is_in_restart_window()` guard in `_broker_ibkr.py` refuses connects 23:30–00:15 ET
