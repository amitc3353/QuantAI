# Architecture Decisions — QuantAI

## ADR-001: Adopt Graphify for codebase knowledge graphs
**Date:** 2026-04-25
**Status:** Accepted
**Context:** QuantAI's pipeline, guard engine, error catalog, and runbooks are
spread across ~80 files. Every Claude Code session pays a re-read tax to
rebuild structural context. Same problem on KARNA's surface (SOUL/AGENTS
files + OpenClaw config). Need a queryable, token-efficient knowledge graph
both Claude Code and OpenClaw can consume.

**Decision:** Install `graphifyy` via `pipx` for the `trader` user. Build a
graph of the QuantAI repo at `graphify-out/graph.json`. Expose it as an
on-demand MCP server registered in `.claude/settings.local.json`. Install
`graphify hook install` for AST-only rebuilds on commit (no LLM cost on
code-only commits). LLM extraction rebuilds remain human-initiated.

**Alternatives considered:**
- Roll our own ctags + grep workflow: rejected — no concept/doc layer, no
  cross-modal edges (code ↔ runbooks).
- Pure embedding-based RAG (e.g., LlamaIndex): rejected — graphify's
  topology-based clustering matches our needs better and gives us paths
  ("what calls this?") that vector search doesn't.
- Skip and keep grepping: rejected — re-read tax compounds as the codebase grows.

**Consequences:**
- (+) 44.8× token reduction per query vs reading raw files (measured on this corpus).
- (+) Free incremental AST rebuilds on every commit.
- (+) `post()` and `run_guard_pipeline()` identified as cross-community god nodes.
- (-) New tool dependency; pinned by `pipx` install, may need version bumps.
- (-) Graph staleness possible after doc changes — manual rebuild step required.
- (-) `graph.json` committed to repo (1.1 MB on initial build; monitor growth).

**Out of scope for this ADR:**
- KARNA self-knowledge graph (Use Case B — separate ADR when implemented).
- Cross-project combined graph (Use Case D — deferred indefinitely).
