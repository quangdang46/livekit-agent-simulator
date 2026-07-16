# AGENTS.md — livekit-agent-simulator

Standalone Python package: MCP + `lk-sim` CLI. Dials **any** LiveKit voice agent using
`.agent-sim/` in a **target repo** (config, scenarios, reports). The agent under test is a
black box — we never import or edit target application code unless the user asks.

---

## Boundary

| In scope | Out of scope |
|---|---|
| `src/livekit_agent_simulator/` | Target agent source, consumer app code, DB, env |
| Scenario JSONL, Script timing, observer, reports | Parsing project-specific dispatch keys in core |
| LiveKit room + dispatch + sim caller | Agent model stack, tools, business rules |

**Opaque dispatch:** `config.yaml` → `livekit.dispatch_metadata` and scenario `Dispatch.metadata`
are passed through as JSON strings. Core Python must not interpret consumer-specific keys.

**Target repo** = path passed as `project_root` / `--root`. Consumer wiring examples live in
`docs/portability.md` — load that file only when the task is target `.agent-sim/` setup, not
for package bugs or features.

---

## Product rule: generic core, not fit-to-one-repo

This package ships **tools + core capabilities** that every LiveKit agent repo can use.
It is **not** a glue layer for one consumer (worker, dashboard, language, brand).

| Do | Do not |
|---|---|
| Build features every target can enable via config / scenario / plugins | Hardcode language, timezone, agent IDs, data topics, or business strings in `src/` |
| Give **extension points** (opaque dispatch, `observe.*`, Script, verify plugins) so users customize | Parse or special-case consumer keys in core Python |
| Put project-specific wiring only in **that target’s** `.agent-sim/` | Change package defaults to match the last repo we smoked |
| Prefer one clear API (`record_audio`, not aliases) | Keep “legacy” flags, dual names, or compatibility shims “just in case” |

**Customization belongs to the user.** We ship knobs and contracts; the target fills
`config.yaml`, scenarios, plugins. If something only works for one monorepo, it is
wrong for core — fix the design or keep it out of `src/`.

**Dev-stage cleanliness (repo is still evolving):**

- No legacy paths. Delete dead config, unused fields, and half-features in the same change.
- Defaults must be **portable** (`en-US` / `UTC` in core; demos may override in templates or target config).
- Docs/examples use neutral placeholders (`yourProjectKey`, `/path/to/target-repo`) — not a real product name as the default.
- Prefer fail-fast or remove over silent multi-provider stubs that only implement one backend.

---

## Product rule: no stubborn patches (defaults first)

A **normal human caller** is the default product, not a special scenario case.

| Do | Do not |
|---|---|
| Prefer existing defaults / one clear gate (e.g. hang_up waits for agent reply) | Stack scenario delays, extra waits, persona constraints, or authoring warns to paper over a bad override |
| If a run feels unnatural, ask: “was a default turned off?” | Treat “don’t hang up right after saying your name” as a one-off backchannel/noise carve-out |
| Fix the **wrong override** or the **broken knob** once | Add compensating sleeps, style flags, or “human reminder” prose in every JSONL |
| Revert stubborn patches when the user says the behavior is just normal | Leave half-fixes in `src/` after the real fix was “leave the default on” |

**Smell test:** if the patch only makes sense for one scenario id or one failure screenshot, it is probably wrong. Delete it and use the default path.

---

## Research before implement or fix (mandatory)

Do **not** guess SDK wire formats, Gemini Live quirks, or LiveKit dispatch behavior.
Complete this loop before non-trivial code changes; repeat if verification fails.

```
Hypothesis → Exa / docs → .venv proof → src/ or report → fix → pytest
```

| Order | When | Where |
|---|---|---|
| 1 | Errors, prior art, API changes, regressions | **Exa** (`web_search_exa`, `web_fetch_exa`); note if using web fallback |
| 2 | LiveKit dispatch, rooms, transcription, agents | **LiveKit MCP** (`docs_search`, `get_pages`, `code_search`) |
| 3 | Gemini Live input/output, modalities, close codes | Exa + **`google-genai` in `.venv`** (`site-packages/google/genai/`) |
| 4 | Types / methods actually imported | **Installed packages** in `.venv`: `livekit`, `livekit-api`, `google-genai` |
| 5 | Our behavior vs expectation | `src/` + failing `reports/<run-id>/events.jsonl` |

**Rules**

- If docs and `.venv` disagree, trust **`.venv`** (what we run).
- Cite real paths (file + symbol) in commits and chat — no “the SDK supports X” without proof.
- Re-research when the first hypothesis fails; do not patch gaps with guesses.
- One-line typos / test-only edits: still read the target file; Exa optional.

---

## Default workflow

1. Read this file.
2. Classify: **package code** (`src/`, `tests/`) vs **target `.agent-sim/` only** (scenarios/config).
3. Run the research loop above for anything beyond typos.
4. Minimal diff → verify:

```bash
uv sync --extra dev
uv run pytest -q
```

On Windows, if `uv sync` fails (MCP exe locked):

```bash
.venv\Scripts\python.exe -m pytest -q
```

| Task | Approach |
|---|---|
| Bug / SDK / protocol | Exa + LiveKit MCP + `.venv` → fix → pytest |
| New scenario kind / MCP tool | Research first; plan if large; tests required |
| Target scenario/config only | Edit `<target>/.agent-sim/` — no package release |
| Smoke against running agent | `lk-sim preflight` + `lk-sim execute <id> --root <path>` (same ops as MCP) |

---

## Layout

| Path | Role |
|---|---|
| `config.py` | Load `.agent-sim/config.yaml` |
| `scenario.py` / `script_parse.py` / `script/` | JSONL + timed Script cues (runtime / verify / summary) |
| `script_runner.py` | Re-exports `script` (stable import path) |
| `run_orchestrator.py` | End-to-end run (phased) |
| `livekit/` | Room, dispatch, observer |
| `gemini/` | Sim caller bridge + optional judge |
| `logging/` | Event envelope, SQLite, reports |
| `web/` (repo root) | Web UI — Vite/TS; `pnpm build` → `web/dist/`; CI force-includes into wheel as `web_static` |
| `src/.../web/` | Report player API (`cues`, markers, HTTP server) |
| `mcp_server.py` / `cli.py` | MCP tools + `lk-sim` |
| `templates/` | Init scaffolds |
| `tests/` | pytest |
| `docs/portability.md` | Optional consumer wiring (not default agent context) |
| `docs/smoke-test.md` | First end-to-end run |

---

## Scenario JSONL (`agent-sim/v1`)

```
Scenario → Persona → [Context] → [Simulator] → [Execute] → [Dispatch] → [Script] → [PassCriteria]
```

- **Execute** — run params; overrides Simulator.
- **Dispatch** — opaque metadata for `RoomAgentDispatch`.
- **Script** — timed caller cues (`agent_speaking` + `delay_ms`); `delivery: room_pcm` plays WAV into sim mic; log verify via `script_verify` and optional **verify plugins** (`docs/plugins.md`).
- **PassCriteria** — optional LLM judge rubric.
- **Context.notes** — author-only (reports/docs); **not** injected into the caller SI.
- **Context.caller_knows** / **world** — optional facts the persona already knows (injected).

---

## Hard rules

- No target-repo application code changes unless explicitly requested.
- No consumer env vars in `pyproject.toml` or core config schema.
- Credentials only in target `.agent-sim/config.yaml` (gitignored).
- Core stays **repo-agnostic**; consumer fit only under target `.agent-sim/` (or docs examples).
- No legacy shims / dual config names — clean breaks are fine while pre-1.0.
- **No stubborn patches** — defaults first; do not compensate with scenario/authoring hacks (see above).
- **pytest must pass** before reporting done.

---

## Naming

| Item | Value |
|---|---|
| Package | `livekit-agent-simulator` |
| CLI | `lk-sim` |
| MCP entry | `lk-sim mcp` (or console script `lk-sim-mcp`) |
| Dot folder (target) | `.agent-sim/` |
| Sim participant | `lk-sim-caller` |
| Room prefix | `lk-sim-<run-id>` |
