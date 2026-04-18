# Runbook: debit strategy rejected by credit-floor guard

## Detection
- Pattern: `below credit floor`, `rejected: estimated_credit`, or diagonal/debit trade dropped at guard step.
- Symptom: Debate approves a trade, but guards reject with a credit-floor message. No execution.

## Diagnosis
1. Which strategy was rejected? Diagonals, debit spreads, and certain calendars have negative `estimated_credit` by design (they're debit strategies).
2. Look in `autonomous_execution.py` guard block for the credit-floor check. Is the bypass flag applied for debit strategies?
3. Check the debate output — did the judge approve a diagonal/debit strategy? If yes and the guard rejected it, the bypass flag is the missing piece.

## Fix
- For diagonals/debits, the guard must allow negative `estimated_credit`. The debit-strategy bypass was added in commit `6e012c0`. Confirm the strategy type string matches (`diagonal`, `bull_call_spread`, etc.) and the bypass branch is hit.
- For credit strategies with small premiums: raising the floor is intentional — leave alone unless the floor is mis-set.

## Auto-fixable?
**Yes — `skip`.** The trade simply doesn't enter. Next cycle scans again; if conditions still favor entry, it'll re-propose. Logging and moving on is the right behavior.

## Prevention
- All debit strategy types should be in the bypass list. Audit the list when adding a new strategy.
- Guard log messages should name the strategy and the threshold, to make diagnosis fast.
