# Agent instructions

This repo is developed *with* AI coding agents and *contains* AI agents.
If you are an AI coding agent working on this codebase, read
[`CLAUDE.md`](CLAUDE.md) first — it carries the full operating contract:
security rules (never read `.env` or credentials), damage-prevention
rules (backups before edits, no changes outside the working folder),
pacing rules (plan-first, 3-attempt debug budget), and the architecture
map. Those rules apply to every agent, not just Claude.

Quick orientation:

- Live system: `v2/shared-data/scripts/` (cron-driven, flat scripts).
- Retired v1 stack: `legacy/` — do not "fix" it; it's kept for history.
- Tests: `v2/shared-data/tests/` (`python3 -m pytest unit/ integration/ -q`).
- Deterministic safety gates (`_*_gate.py`, `beta/risk_engine.py`,
  `gamma/risk_check.py`) and the Gamma experiment internals are
  guarded paths — treat them as read-only unless the task is explicitly
  about them.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
