# Dashboard Architecture (2026-05-02)

A snapshot of how the live QuantAI dashboard is structured, written after a
discoverability mistake where the wrong renderer was edited. Read this **before**
touching anything dashboard-related.

## Where the files live

| Path | Role | Owner | Tracked? |
|---|---|---|---|
| `/home/trader/dashboard/index.html` | **Canonical source.** Hand-edited React 18 SPA, ~88 KB. This is what you edit. | trader:trader | No — audit trail is `.bak.YYYY-MM-DD-*` siblings. |
| `/var/dashboard/index.html` | **Deployed copy** that the HTTP server serves. Synced from source via `sudo cp`. | trader:trader | No |
| `/var/dashboard/state/*.json` | Per-collector state files. The dashboard polls these via `fetch('/state/{name}')`. | root:root | No (generated) |
| `/var/dashboard/collect_*.py` | One collector per tile. Each writes its own state file atomically. Cron-driven. | root:root | No |
| `/var/dashboard/generate.py` | **Deprecated.** Used to produce a simple tile-grid HTML via the (now-disabled) `dashboard-generator.service`. Do not edit it expecting changes to appear in the live dashboard. | root:root | No |
| `dashboard-http.service` | systemd unit. `python3 -m http.server 8080 --bind 127.0.0.1` serving `/var/dashboard/`. | — | — |
| `dashboard-generator.service` | systemd unit. Disabled since 2026-04-17 (was running `generate.py --loop`). | — | — |

The whole dashboard lives outside the QuantAI git repo — there's no automated build, no symlink between source and deployed copy. Editing the source requires a manual sudo cp to deploy.

## Source-file structure

`/home/trader/dashboard/index.html` is one self-contained HTML file:

- **Lines 1–24** — head: React 18 + Babel-standalone + Recharts + Mermaid + Tailwind (all from CDN)
- **Line 24+** — single `<script type="text/babel" data-presets="react">` block; in-browser JSX compilation
- **Lines 29–50** — `STATE_FILES` array. Adding a new tile means adding the JSON filename here.
- **Lines 92–153** — shared UI primitives: `Dot`, `Badge`, `AgentBadge`, `Card`, `StatusDot`
- **Tab components** (one per tab):
  - `LiveTab` (line 183)
  - `AgentsTab` (line 439)
  - `TradesTab` (~1223)
  - `PerformanceTab` (~1370)
  - `SelfLearningTab` (~1543) ← added 2026-05-02
  - `SystemTab` (~525)
  - `WorkflowsTab` (~857) — the mermaid diagrams
  - `ErrorsTab` (~887)
  - `HistoryTab` (~1106)
- **`WORKFLOWS` array** (line 718–~898) — array of `{key, name, description, chart}` objects, where `chart` is a mermaid string. WorkflowsTab renders each as an expandable section.
- **`App` component** at the bottom — `tabs` array (~1830) and the `tab === "X" && <XTab />` routing block (~1840).

## How a new tile / tab is added

1. Write a collector at `/var/dashboard/collect_<name>.py` that produces `quantai-<name>.json` in `/var/dashboard/state/`. Match the JSON contract: `{"last_updated": ISO, "status": "ok|warning|stale|idle", "data": {...}}`.
2. Add a cron entry under root's crontab (typical interval: 1–5 min).
3. Add `"quantai-<name>.json"` to `STATE_FILES` in `/home/trader/dashboard/index.html`.
4. Add a new `function <Name>Tab({ state }) { ... }` component, reading `state["quantai-<name>.json"]`.
5. Add `{key: "<name>", label: "<Label>"}` to the `tabs` array.
6. Add `{tab === "<name>" && <NameTab state={state} />}` to the routing block.
7. Deploy: `sudo cp /home/trader/dashboard/index.html /var/dashboard/index.html`.

No build step, no bundler. Babel-standalone compiles JSX in the browser at page load.

## What landed on 2026-05-02

- **New tab: Self-Learning** (component at ~line 1543, tab routing wired). Reads `quantai-learning.json` produced by `/var/dashboard/collect_learning.py`. Empty state until first trade closes.
- **Updated WORKFLOWS diagrams:**
  - `pipeline` — annotated `decision` field on journal write, `$50k cap` filter on scan.
  - `alpha` — annotated 2% × $50k = $1k max loss target.
  - `beta` — added `effective_equity` and 1% × $50k = $500 sizing nodes.
  - `monitor` — added inline diagnose + review hooks after journal close (with their data-flow arrows to `capability_requests/` and `trade_reviews/`).
- **New WORKFLOWS diagrams:**
  - `self-learning` — full diagnose → review → weekly synthesis loop, including resolve_item.py CLI feeding learning_tracker.json.
  - `graphify` — post-commit AST hook + monthly LLM-driven `--update` via Claude Code headless, with the failure path to Discord + dashboard error.

## Companion files (in repo)

These files live under `v2/shared-data/scripts/` (in the QuantAI git repo) and feed the dashboard:

- `resolve_item.py` — CLI to mark items resolved; writes `learning_tracker.json`.
- `weekly_synthesis.py` — Friday synthesis writer (Sonnet).
- `agent_self_diagnosis.py` + `trade_reviewer.py` — per-trade Haiku writers.
- `monthly_graph_refresh.py` — wraps `claude -p '/graphify --update'`.

## Common pitfalls

- **Editing `/var/dashboard/generate.py` does nothing visible.** The dashboard-generator service is disabled. The only thing live is the React SPA at `/var/dashboard/index.html`.
- **Editing `/var/dashboard/index.html` directly is volatile.** Any future deployment from `/home/trader/dashboard/index.html` will overwrite it. Always edit the canonical source.
- **`dashboard-http.service` is just a static file server** — there's no Python rendering at request time. The browser does all the rendering via React + Babel.
- **Adding a Recharts chart type** requires either destructuring it in line 27 or referencing `Recharts.<Component>` directly. Bar charts aren't currently destructured.
- **Mermaid renders client-side**; bad syntax shows an error block in the diagram cell, doesn't crash the tab.
