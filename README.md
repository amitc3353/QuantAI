# QuantAI

Autonomous multi-agent options-trading system. Four narrow Python agents
operate a $1 M IBKR paper account during US market hours, enforce every
safety rule in code (not prompts), and run on a single VPS for ~$4–5/month
in LLM spend.

> **Use LLMs only where judgment is needed. Enforce all safety rules in
> code, not in prompts.**

Most of what makes QuantAI interesting follows from taking that sentence
seriously.

---

## Documentation map

| doc | what it covers |
|-----|----------------|
| [`docs/ARCHITECTURE_SUMMARY.md`](docs/ARCHITECTURE_SUMMARY.md) | 10-minute public-facing tour — **start here** |
| [`docs/architecture.md`](docs/architecture.md) | Full 2 500-line deep reference (§0–§22) |
| [`docs/STATE.md`](docs/STATE.md) | Live operational snapshot (positions, halts, bugs) |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | Deferred work and known debt |

---

## System topology

```
                    ┌──────────────────────────────────────────┐
                    │  Operator (Amit)                          │
                    │  • phone Discord ✅ approvals             │
                    │  • Cowork SSHFS sessions for code edits   │
                    └──────────┬────────────────────────┬───────┘
                               │                        │
                  Discord bot  │                        │ SSHFS
                               ▼                        ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                      VPS  (<redacted>)                           │
   │                      Tailscale <redacted>                        │
   │                                                                  │
   │  ┌───────────────────────────────────────────────────────────┐  │
   │  │  KARNA / OpenClaw  (24/7 Claude Sonnet 4.6)                │  │
   │  └─────┬─────────────────────────────────┬───────────────────┘  │
   │        │ tools                        │ Discord bot              │
   │        ▼                              ▼                          │
   │  ┌─────────┐    cron (UTC)   ┌─────────────────────────┐        │
   │  │ ClawRoute│◄────────────── │ 4 agents + monitoring   │        │
   │  │ :18790   │   LLM ingress  │                         │        │
   │  │ tier rt  │                │ Alpha · Beta · Gamma    │        │
   │  └────┬────┘                 │ Sentinel                │        │
   │       │                      │ heartbeat / position /  │        │
   │       │                      │ system / error monitors │        │
   │       │                      └─────────┬───────────────┘        │
   │       │                                │                         │
   │       ▼                                ▼                         │
   │  ┌─────────┐                ┌────────────────────┐               │
   │  │Anthropic│                │ broker.py adapter   │               │
   │  │  API    │                │ BROKER_TYPE env var │               │
   │  └─────────┘                └─────┬─────────┬────┘               │
   │                                    │         │                   │
   │                            primary │         │ fallback          │
   │                                    ▼         ▼                   │
   │                           ┌──────────┐  ┌──────────┐             │
   │                           │  IBKR    │  │ Alpaca   │             │
   │                           │ Gateway  │  │ Paper    │             │
   │                           │ :4002    │  │ REST     │             │
   │                           └────┬─────┘  └────┬─────┘             │
   │                                └──────┬──────┘                   │
   │                                       ▼                          │
   │                          ┌──────────────────────────┐            │
   │                          │ trades.jsonl              │            │
   │                          │ (append-only journal)     │            │
   │                          │  A### / B### / G###       │            │
   │                          └─────┬──────────────┬─────┘            │
   │                                │              │                  │
   │                                ▼              ▼                  │
   │                       ┌────────────┐   ┌──────────────┐          │
   │                       │ collectors │   │ self-learning │          │
   │                       │ (cron 1-15m)│   │ loop          │          │
   │                       └─────┬──────┘   └─────┬────────┘          │
   │                             ▼                ▼                   │
   │                      ┌─────────────────────────────┐             │
   │                      │ Dashboard (React SPA)       │             │
   │                      │ python3 -m http.server 8080 │             │
   │                      └────────────┬────────────────┘             │
   └───────────────────────────────────┼──────────────────────────────┘
                                       │
                                       ▼ Tailscale serve
                          https://quantai.tail1465ff.ts.net/
```

---

## The four agents

| agent | trades | decision method | LLM calls/cycle | trade-ID prefix |
|-------|--------|-----------------|-----------------|-----------------|
| **Alpha** | ETF/equity spreads (78-ticker universe) | Sonnet debate chamber (Bull/Bear/Judge) | 2–3 | `A###` |
| **Beta** | SPX / XSP / VIX index options | 12-regime deterministic classifier → 8 strategies | 0 | `B###` |
| **Gamma** | Bull call debit spreads (Connors RSI mean-reversion) | Deterministic scan + re-validate | 0 | `G###` |
| **Sentinel** | Does not trade | LLM triage of logs/errors → auto-fix or Discord card | 1–2 | — |

All four agents write to a single append-only JSONL journal. One broker
adapter (`broker.py`) with `BROKER_TYPE` env var handles order routing.
A position monitor closes positions and reconciles journal vs. broker
state.

Index options (Beta) provide Section 1256 tax treatment (60/40
long/short), European exercise, and cash settlement — structural
advantages that equity-ETF options don't offer.

---

## Safety: 12 hard rules in code

Every safety rule is enforced in Python, not in prompts.

Highlights — the full list is in
[`docs/architecture.md` §16](docs/architecture.md):

- Per-trade max loss capped at 2 % of effective sizing
- Daily entry budget counted from journal writes (crash-safe)
- `place_mleg_order` returns `None` on failure, never raises
- `verify_legs_flat()` confirms broker-side zero before journal CLOSED
- VIX ≥ 35 → HALT regime, no trading
- Earnings blackout: 14 days (Alpha), 7 days (Gamma)
- Sentinel: path-allowlisted away from trading code, journal, broker,
  and openclaw service; 3-attempt budget per fix

---

## Test coverage

782 tests (unit + integration) gate every push via a pre-push hook.

Recent focused work has driven the deterministic safety layer
toward higher coverage — see [`docs/BACKLOG.md`](docs/BACKLOG.md)
for the full plan. Notable targeted suites:

- `beta/risk_engine.py` — per-source risk gates (Rules 1–6 of §16)
- `_broker_ibkr.py` — Phase 5b partial-fill safeguard (the
  `order_submitted` flag, async flush, recovery via
  `_find_open_order_by_ref`, and `verify_legs_flat`)

Run with:

```bash
cd v2/shared-data/tests
python3 -m pytest unit/ integration/ -q
```

---

## Quick start (development)

```bash
# Clone
git clone https://github.com/amitc3353/QuantAI.git && cd QuantAI

# Run tests (requires pytest; deps are system-installed on VPS)
cd v2/shared-data/tests && python3 -m pytest unit/ integration/ -q

# Full system requires a VPS with IB Gateway, ClawRoute, OpenClaw,
# and environment variables — see SETUP_GUIDE.md for deployment.
```

---

## Design principles

- **LLMs only where judgment is needed.** Beta and Gamma are zero-LLM.
  Alpha uses LLMs for the debate; everything else is Python.
- **Cost-conscious.** ClawRoute tier-routes cheap tasks to cheap models.
  Total LLM spend: ~$4–5/month.
- **Data sovereignty.** Everything on one VPS. No external services
  beyond LLM providers and brokers.
- **Cron is the metronome.** No message bus, no orchestrator, no
  long-lived process. Each script runs to completion and exits.
- **Journal is the single source of truth.** Append-only JSONL. Every
  analytics question, every dashboard view, every self-learning pass
  reads from this one file.
