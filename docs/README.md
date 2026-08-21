# Documentation index

## Start here

| Doc | What it is |
|---|---|
| [`ARCHITECTURE_SUMMARY.md`](ARCHITECTURE_SUMMARY.md) | 10-minute prose tour of the whole system |
| [`architecture.md`](architecture.md) | The deep reference (~2,500 lines) — every component with file paths, an ELI10 intuition, and an honest good/bad/could-be-better verdict |
| [`STATE.md`](STATE.md) | Dated operational snapshot; ages fast by design |

## Decisions (ADRs)

- [`00-ARCHITECTURE-DECISIONS.md`](00-ARCHITECTURE-DECISIONS.md) — ADR-001 (knowledge graph), **ADR-002 (LLM cost discipline via a single ingress)**, ADR-004 (Alpaca → IBKR migration), ADR-005 (Agent Beta). ADR-003 was retired before acceptance; the gap is deliberate.

## Postmortems & incident engineering

- [`2026-06-02-gamma-incident-recovery.md`](2026-06-02-gamma-incident-recovery.md) — the duplicate-ID cascade that inverted broker positions
- [`2026-06-03-recovery-complete.md`](2026-06-03-recovery-complete.md) — recovery timeline, broker-authoritative P&L, shipped fixes with commit SHAs
- [`2026-06-01-fsm-resurrection-gap.md`](2026-06-01-fsm-resurrection-gap.md) — root-cause: phantoms that had actually filled
- [`2026-05-30-trade-lifecycle-fsm-plan.md`](2026-05-30-trade-lifecycle-fsm-plan.md) — the FSM refactor plan those incidents motivated

## Runbooks & error taxonomy

- [`runbooks/`](runbooks/) — 12 operational runbooks
- [`error-catalog.json`](error-catalog.json) — 308-entry machine-readable taxonomy, auto-grown weekly by `error_learner.py`, wired to the runbooks

## Experiments

- [`gamma-four-arm-ab-test-plan.md`](gamma-four-arm-ab-test-plan.md) — pre-registered 4-arm ranking experiment, 9 locked design decisions
- [`gamma-universe-expansion-proposal.md`](gamma-universe-expansion-proposal.md) → [`implementation plan`](gamma-universe-expansion-implementation-plan.md) — 27 → 155 symbols

## Hardening (current phase)

- [`HARDENING.md`](HARDENING.md) — severity-rated tracker; every item carries a required proof-test
- [`2026-08-12-hardening/findings.md`](2026-08-12-hardening/findings.md) — five code-level deep-dives, every claim cited to file:line
- [`FINDINGS.md`](FINDINGS.md) — repo-audit tracker
- [`BACKLOG.md`](BACKLOG.md) — deferred work and known debt

## Archive

- [`archive/`](archive/) — dated one-off planning docs, superseded guides, and historical operator notes. Kept for the record, not maintained.
