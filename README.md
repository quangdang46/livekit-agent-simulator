# livekit-agent-simulator

<div align="center">
  <img src="lk_sim_illustration.webp" alt="livekit-agent-simulator — AI caller vs LiveKit voice agent with forensic reports" width="720">
</div>

<div align="center">

![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-blue.svg)
![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.13-blue.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/quangdang46/livekit-agent-simulator/actions/workflows/ci.yml/badge.svg)](https://github.com/quangdang46/livekit-agent-simulator/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/quangdang46/livekit-agent-simulator)](https://github.com/quangdang46/livekit-agent-simulator/releases)

</div>

**Dial any LiveKit voice agent with an AI simulated caller — and keep a full forensic log.**  
Standalone MCP server + CLI (`lk-sim`). Black-box testing: no imports from the agent under test, no edits to its code or `.env`.

<div align="center">
<h3>Quick Install</h3>

```bash
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --verify
```

</div>

### Install via coding agent (copy-paste)

Paste into Claude Code, Cursor, Codex, AmpCode, Windsurf, or any coding agent **from the repo you want to test**:

```text
Install and configure livekit-agent-simulator (CLI: lk-sim) for this project by following the instructions here:
https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/docs/guide/installation.md

Target project root is this workspace. Use absolute --root paths. Install the portable CLI if missing, run lk-sim init, help fill .agent-sim/config.yaml from my local env or ask me for LiveKit + Gemini + agent_name, ensure .agent-sim is gitignored, run preflight, and stop before execute if the voice agent worker is not running. Do not edit agent application source outside .agent-sim/.
```

Same idea, one line:

```text
Install and configure livekit-agent-simulator by following: https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/docs/guide/installation.md
```

---

## TL;DR

### The Problem

Voice agents fail in ways unit tests never see:

| Gap | What you miss |
|-----|----------------|
| No real caller | Scripts that never interrupt, stall, or switch language |
| Chat-only evals | No room events, audio timing, or tool spans |
| Manual QA calls | Not CI-reproducible, no structured PassCriteria |
| Agent-coupled harnesses | Tests break when you refactor the worker |

### The Solution

**livekit-agent-simulator** joins a fresh LiveKit room as `lk-sim-caller`, drives a Gemini Live persona from a scenario JSONL, observes transcripts / tools / flow / room events, and writes a timestamped report you can play back.

| Surface | What you get |
|---------|--------------|
| `lk-sim` CLI | init → preflight → execute → report → web |
| MCP server | Same ops for Claude Code, Cursor, Codex, … |
| Reports | `events.jsonl`, `timeline.md`, `summary.json`, optional stereo WAV |
| Judge | Optional LLM PassCriteria scoring |

### Why Use lk-sim?

| Feature | What it does |
|---------|--------------|
| **Black-box dispatch** | Only needs `agent_name` + LiveKit creds |
| **Scenario JSONL** | Persona, Execute, Script, PassCriteria, Dispatch |
| **Forensic log** | Per-turn events in SQLite + `reports/<run-id>/` |
| **Report player** | Local web UI: audio + transcript sync |
| **CLI ↔ MCP parity** | One `ops` layer — no duplicate run paths |
| **Portable packs** | Download installer; no uv/pip required for users |

---

### Quick Example

```bash
# Install once
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --verify

# In the repo you want to test (agent worker must already be running)
lk-sim init --root /path/to/target
# edit /path/to/target/.agent-sim/config.yaml  (LiveKit + Gemini keys, agent_name)

lk-sim preflight --root /path/to/target
lk-sim execute smoke-hello --root /path/to/target
lk-sim report <run-id> --root /path/to/target
lk-sim web --root /path/to/target          # Ctrl+C to stop
```

---

## Design Philosophy

1. **The agent under test is a black box.**  
   We never import or patch target application code. Dispatch metadata is opaque JSON.

2. **Generic core, target-owned config.**  
   Language, timezone, topics, and business strings belong in the target’s `.agent-sim/` — not hardcoded in the package.

3. **One ops layer for CLI and MCP.**  
   `execute_*` validates then runs. No “run vs execute” forks.

4. **Forensics over vibes.**  
   Every run produces structured events you can `compare`, `log`, and play back.

5. **CI-friendly gates.**  
   Hard fails on status / assert / script; optional strict judge for softer LLM scoring.

---

## How It Works

```text
1. Read <target>/.agent-sim/config.yaml
2. Create room lk-sim-<run-id> + RoomAgentDispatch(agent_name)
3. Join as lk-sim-caller; bridge audio to Gemini Live persona
4. Observe: lk.transcription, data topics, timing, interruptions
5. Write reports/<run-id>/ + runs.sqlite
6. Optional LLM judge vs PassCriteria
```

```text
┌──────────────────┐     dispatch      ┌──────────────────────┐
│  Gemini Live     │ ───────────────▶  │  Your LiveKit agent  │
│  sim caller      │   room audio      │  (black box worker)  │
│  (scenario)      │ ◀───────────────  │                      │
└────────┬─────────┘                   └──────────────────────┘
         │ observe
         ▼
┌─────────────────────────────────────┐
│ reports/<run-id>/                   │
│  events.jsonl · timeline.md         │
│  summary.json · conversation.wav    │
│  + runs.sqlite · optional judge     │
└─────────────────────────────────────┘
```

---

## How lk-sim Compares

| Approach | Real room | AI caller | Forensic log | MCP | Black-box |
|----------|-----------|-----------|--------------|-----|-----------|
| Manual phone QA | ✅ | ❌ | ❌ | ❌ | ✅ |
| Unit / mock STT | ❌ | ❌ | Partial | ❌ | ❌ |
| In-repo agent tests | ⚠️ | ⚠️ | Varies | ❌ | Often coupled |
| **lk-sim** | ✅ LiveKit | ✅ Gemini Live | ✅ Full | ✅ | ✅ |

**When to use lk-sim:**
- Regression suites for LiveKit voice agents
- Agent-driven CI / coding-agent workflows (MCP)
- Debugging turn-taking, tools, and silence without reading agent source

**When it might not be ideal:**
- Pure text chatbots with no LiveKit room
- Offline environments without LiveKit + Gemini API access

---

## Installation

### Quick install (recommended)

**Download only — no uv/pip/build on your machine.** CI ships a portable pack (embedded Python + deps + report player).

```bash
# macOS / Linux
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --verify
```

```powershell
# Windows PowerShell
irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" -OutFile "$env:TEMP\lk-sim-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lk-sim-install.ps1" -Verify
```

Also available from a release asset:

```bash
curl -fsSL "https://github.com/quangdang46/livekit-agent-simulator/releases/download/v0.1.0/install.sh" \
  | bash -s -- --verify
```

| Flag | Purpose |
|------|---------|
| `--verify` | Checksum verification |
| `--ref v0.1.0` | Pin release tag |
| `--no-mcp` | Skip MCP registration into coding tools |
| `--uninstall` | Remove install |

By default the installer registers the MCP server `livekit-agent-simulator` (`lk-sim mcp`) into detected tools: Claude Code, Cursor, Cline, Windsurf, VS Code Copilot, Gemini CLI, Amazon Q, OpenCode, Codex, Warp.

**Agent-oriented install playbook (long form):** [docs/guide/installation.md](docs/guide/installation.md)  
Raw URL for paste into agents:  
`https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/docs/guide/installation.md`

### From source (maintainers / contributors)

```bash
git clone https://github.com/quangdang46/livekit-agent-simulator.git
cd livekit-agent-simulator
uv sync --extra dev
uv run lk-sim --help
```

Requires **Python 3.10–3.13**.

### Report player (maintainers)

Users never build this — CI packs it. Source: `web/` (standard Vite → `web/dist/`).

```bash
pnpm --dir web install
pnpm --dir web build                    # → web/dist/
python scripts/bundle_report_player.py  # → templates/report-player/ (wheel only)
pnpm --dir web dev                      # HMR; proxy /api + /runs → lk-sim web :8765
```

See `web/README.md`.

---

## Quick Start

```bash
# Agent worker must be running and registered with LiveKit
lk-sim guide
lk-sim init --root /path/to/target
# fill .agent-sim/config.yaml

lk-sim preflight --root /path/to/target
lk-sim scenario-init smoke-hello --root /path/to/target   # if needed
lk-sim validate smoke-hello --root /path/to/target
lk-sim execute smoke-hello --root /path/to/target
lk-sim runs --root /path/to/target
lk-sim report <run-id> --root /path/to/target
lk-sim web --root /path/to/target
```

### Minimal scenario (`smoke-hello`)

```jsonl
{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"smoke-hello","locale":"en-US","tags":["smoke"]}}
{"kind":"Persona","spec":{"name":"Alex","brief":"First-time caller; confirm you reached the right place, then end politely.","goals":["Hear the agent","Say you will call back"],"style":"polite, brief"}}
{"kind":"Execute","spec":{"max_turns":2,"timeout_s":90,"first_speaker":"user"}}
{"kind":"PassCriteria","spec":{"criteria":["The agent responded to the caller","The agent responded in the caller's language"]}}
```

Full-line `//` comments in scaffolded JSONL are guides — runtime ignores them.

---

## Configuration

Target-only data lives under `<target>/.agent-sim/` (**gitignored**). Created by `init`.

| Section | Required | Purpose |
|---------|----------|---------|
| `livekit.url` | yes | `wss://…` LiveKit Cloud or self-host |
| `livekit.api_key` / `api_secret` | yes | Server API credentials |
| `livekit.agent_name` | yes | Must match worker dispatch name |
| `livekit.dispatch_metadata` | no | Default opaque JSON **string** for all runs |
| `simulator.google_api_key` | yes | Gemini key for sim caller (+ judge) |
| `simulator.voice.model` / `voice` / `language` | no | Defaults: flash-live, Puck, `en-US` |
| `judge.model` | no | If set + PassCriteria → post-run LLM judge |
| `observe.record_audio` | no (default `true`) | Local stereo WAV (L=sim, R=agent); no Egress |
| `observe.data_topics` | no | Empty = all topics |
| `observe.tool_event_patterns` | no | Map data payloads → tool start/end/error |

See template: [`templates/config.yaml`](templates/config.yaml). Consumer-specific wiring: [`docs/portability.md`](docs/portability.md).

---

## Commands

CLI and MCP share the same public ops (`ops.py`). Prefer `execute` (validate then run).

| CLI | MCP tool | Purpose |
|-----|----------|---------|
| `init` | `init_project` | Scaffold `.agent-sim/` + gitignore |
| `guide` | `guide` | Setup/ops guide (markdown) |
| `web` | `web` | Local report player |
| `preflight` | `preflight` | Config + LiveKit connectivity |
| `scenarios` | `list_scenarios` | List `scenarios/*.jsonl` |
| `plugins` | `list_plugins` | Verify plugins |
| `cues` | `list_cues` | Built-in + local PCM cues |
| `validate` | `validate_scenario` | Schema + lint |
| `export` | `export_scenario` | Parsed scenario JSON |
| `scenario-init` | `init_scenario` | Scaffold JSONL with `//` guides |
| `execute` | `execute_scenario` | Validate then run one scenario |
| `execute-all` | `execute_scenarios` | Batch (ids / tag) |
| `execute-dict` | `execute_scenario_dict` | In-memory scenario dict |
| `status` | `get_run_status` | SQLite run status |
| `log` | `get_run_log` | Filtered `events.jsonl` |
| `report` | `get_run_report` | Summary + verdict + paths |
| `compare` | `compare_runs` | Diff two runs |
| `runs` | `list_runs` | Run history |
| `mcp` | — | Start MCP server (stdio) |

```bash
lk-sim execute smoke-hello --root /path/to/target
lk-sim execute-all --tag smoke --root /path/to/target
lk-sim log <run-id> --root /path/to/target
lk-sim compare <run-a> <run-b> --root /path/to/target
lk-sim web --port 8765 --root /path/to/target
```

Every MCP tool needs `project_root` **except** `guide`.

### MCP config examples

Installer writes this when tools are detected. Manual Cursor:

```json
{
  "mcpServers": {
    "livekit-agent-simulator": {
      "command": "lk-sim",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

Dev checkout (package not installed globally):

```json
{
  "mcpServers": {
    "livekit-agent-simulator": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/livekit-agent-simulator", "lk-sim", "mcp"]
    }
  }
}
```

Equivalent one-shot entry: `lk-sim-mcp` (same process as `lk-sim mcp`).

---

## Architecture

```text
src/livekit_agent_simulator/
├── cli.py / mcp_server.py     # thin surfaces
├── ops.py                     # shared public ops
├── run_orchestrator.py        # room lifecycle + run
├── scenario.py                # JSONL parse / validate
├── config.py                  # .agent-sim/config.yaml
├── preflight.py
├── asserts.py / suite.py      # CI gates
├── gemini/                    # Live caller + judge
├── livekit/                   # room, dispatch, observe
├── audio/ · script/ · plugins/
└── web/                       # report player server
```

| Layer | Role |
|-------|------|
| Target `.agent-sim/` | Config, scenarios, reports, local plugins/cues |
| Package `templates/` | Scaffold defaults + built-in cues |
| LiveKit | Room, dispatch, data topics, transcription |
| Gemini Live | Simulated caller voice + optional judge |

---

## CI / Release

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [CI](.github/workflows/ci.yml) | PR / push → `main` | report-player build, `pytest` (3.10 + 3.12), `lk-sim --help` |
| [Release](.github/workflows/release.yml) | tag `v*` | pytest → wheel → portable packs (win/linux/mac) → GitHub Release |

```bash
# Local check
uv sync --extra dev
pnpm --dir web build
uv run pytest -q

# Release (pre-1.0 may force-retag a single 0.1.0)
git tag v0.1.0
git push origin v0.1.0
```

---

## Troubleshooting

### `preflight` fails connectivity

```bash
lk-sim preflight --root /path/to/target
# Confirm livekit.url / api_key / api_secret and that the project is reachable.
# Skip API check while editing config:
lk-sim preflight --no-connectivity --root /path/to/target
```

### Agent never joins the room

- Worker process must be **running** and registered with the same `livekit.agent_name`.
- Increase `livekit.agent_join_timeout_ms` if cold start is slow.
- Check dispatch metadata is valid JSON **string** if your worker requires it.

### Gemini / simulator auth errors

Set `simulator.google_api_key` in `.agent-sim/config.yaml`. The sim caller uses Gemini Live (`gemini-3.1-flash-live-preview` by default).

### No audio in report player

With `observe.record_audio` enabled (default `true`): `reports/<run-id>/conversation.wav`

```bash
lk-sim web --root /path/to/target
```

### MCP tools not listed

```bash
lk-sim mcp   # must be what the host launches
# or reinstall without --no-mcp
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --verify
```

### Scenario validation errors

```bash
lk-sim validate my-case --root /path/to/target
lk-sim scenario-init my-case --root /path/to/target   # fresh scaffold with // guides
```

---

## Limitations

### What lk-sim Doesn't Do (Yet)

- **Not an agent framework** — it tests agents; it does not implement business tools
- **Not offline-first** — needs LiveKit + Gemini (or configured backends)
- **Not a load generator** — one simulated caller per run (batch via `execute-all`)

### Known Limitations

| Capability | Current state | Notes |
|------------|---------------|-------|
| Black-box dispatch | ✅ | Opaque metadata only |
| Multi-caller rooms | ❌ | Single sim participant |
| Non-Gemini caller backends | ⚠️ | Gemini Live is the supported path |
| Pixel-perfect ASR scoring | ❌ | Use PassCriteria + judge / asserts |
| Secrets in config | ⚠️ Paste in gitignored YAML | Do not commit `.agent-sim/` |

---

## FAQ

### Does it modify my agent repo?

Only scaffolds **`.agent-sim/`** (gitignored). It does not edit agent source.

### CLI vs MCP — which should I use?

Same ops. Use CLI in terminals/CI; MCP inside coding agents. Prefer `execute_*` over ad-hoc run paths.

### How do I pass project-specific dispatch fields?

`livekit.dispatch_metadata` or scenario `Dispatch.spec.metadata` as an opaque JSON string. Core does not parse consumer keys. See [`docs/portability.md`](docs/portability.md).

### Can I assert on tool calls?

Yes — map data topics with `observe.tool_event_patterns`, use Script/assert plugins, and/or PassCriteria + judge. See [`docs/plugins.md`](docs/plugins.md).

### Where are reports stored?

`<target>/.agent-sim/reports/<run-id>/` plus `runs.sqlite` under `.agent-sim/`.

### Is the report player separate?

No — `lk-sim web` serves the prebuilt player from the install pack. Maintainers build from `web/`.

---

## Docs

| Doc | When |
|-----|------|
| [AGENTS.md](AGENTS.md) | Rules for AI agents working on this package |
| [docs/smoke-test.md](docs/smoke-test.md) | First end-to-end run |
| [docs/portability.md](docs/portability.md) | Consumer dispatch / observe setup |
| [docs/plugins.md](docs/plugins.md) | Verify plugins + Python API |
| `lk-sim guide` | On-demand setup/ops guide |

---

## About Contributions

Please don't take this the wrong way, but I do not accept outside contributions for any of my projects. I simply don't have the mental bandwidth to review anything, and it's my name on the thing, so I'm responsible for any problems it causes; thus, the risk-reward is highly asymmetric from my perspective. I'd also have to worry about other "stakeholders," which seems unwise for tools I mostly make for myself for free. Feel free to submit issues, and even PRs if you want to illustrate a proposed fix, but know I won't merge them directly. Instead, I'll have Claude or Codex review submissions via `gh` and independently decide whether and how to address them. Bug reports in particular are welcome. Sorry if this offends, but I want to avoid wasted time and hurt feelings. I understand this isn't in sync with the prevailing open-source ethos that seeks community contributions, but it's the only way I can move at this velocity and keep my sanity.

---

## License

[MIT](./LICENSE)

---

<div align="center">

**Black-box LiveKit agent tests. Real rooms. Forensic reports.**

</div>
