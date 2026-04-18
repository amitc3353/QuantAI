# Runbook: Alpaca connection failure

## Detection
- Pattern: `ConnectionError`, `SPY curl timeout`, `Connection refused`, `credit balance is too low` (Anthropic, not Alpaca, but handled here because the effect is the same — pipeline blocked on external API).
- Symptom: Order placement, position fetch, chain lookup, or debate call fails with a network error.

## Diagnosis
1. `curl -sI https://paper-api.alpaca.markets/v2/account -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY"` — expect 200. 401 = bad keys, 5xx = Alpaca down, timeout = network.
2. For Anthropic: credit balance low means account is empty. Not a connection issue, a funding one.
3. `ping -c 3 paper-api.alpaca.markets` — rule out DNS/network.
4. `systemctl status tailscaled` — no Tailscale = could impact egress if routed through it (usually not, but verify).

## Fix
- **Transient network:** retry (the auto-fixer handles this).
- **Auth failure (401):** rotate keys in Alpaca console, update `/home/trader/QuantAI/.env`, restart any long-running consumers. Don't commit keys.
- **Anthropic credits exhausted:** top up via Anthropic console. Pipeline will resume next cycle automatically.

## Auto-fixable?
**Yes — `retry`** for transient network. The detector re-runs the failing cron target once after a 60s delay. If the second attempt also fails, classification becomes `none` and a Discord alert fires.

## Prevention
- Every Alpaca caller uses a short timeout (10-15s) and retries via cron cadence.
- `autonomous_execution.py` treats a failed order as "not filled" — the trade stays unentered, no journal corruption.
- Anthropic: low-balance alert via Discord should fire earlier than zero. Not yet implemented (captured as a TODO).
