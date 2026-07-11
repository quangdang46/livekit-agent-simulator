# livekit-agent-simulator

Standalone MCP server + CLI (`lk-sim`) that dials **any LiveKit voice agent** with an
AI simulated caller (Gemini Live) and records a full forensic behavior log —
transcripts, tool events, flow events, room events — all timestamped per turn.

**Zero-touch:** the agent under test is a black box. The simulator only needs the
agent's registered `agent_name`; it never reads or modifies the target project's code,
`.env`, or model config.

CLI and MCP expose the **same public ops** (shared `ops.py`). No duplicate “run vs execute”
paths — use `execute_*` to validate then run.

## How it works

1. Reads `<your-repo>/.agent-sim/config.yaml` (LiveKit creds + `agent_name` + simulator voice).
2. Creates a fresh room `lk-sim-<run-id>` and dispatches the agent via `RoomAgentDispatch`.
3. Joins as participant `lk-sim-caller`, bridges audio with a Gemini Live session
   (`gemini-3.1-flash-live-preview`) playing the scenario persona.
4. Observes everything from inside the room: `lk.transcription` text streams, custom
   data topics (when configured), audio timing, interruptions, silences.
5. Writes `reports/<run-id>/` — `events.jsonl`, `timeline.md`, `summary.json`,
   `meta.json`, optional `conversation.wav` — and mirrors to `runs.sqlite`.
6. Optional LLM judge (`gemini-2.5-flash`) scores the transcript + tool spans against
   the scenario's PassCriteria.

## Install (user machine)

```bash
curl -fsSL https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh | bash
# requires uv (or pipx). Then:
lk-sim guide
```

## Quick start

```bash
# In the repo you want to test (agent worker must be running; set `agent_name` in config):
lk-sim init --root /path/to/target
#   → scaffolds .agent-sim/ (gitignored) — fill in config.yaml

lk-sim preflight --root /path/to/target
lk-sim execute smoke-hello --root /path/to/target
lk-sim report <run-id> --root /path/to/target
lk-sim web --root /path/to/target          # audio + transcript player (Ctrl+C to stop)
```

## Cursor MCP config

```json
{
  "mcpServers": {
    "livekit-agent-simulator": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/livekit-agent-simulator", "livekit-agent-simulator-mcp"]
    }
  }
}
```

## Public ops (CLI ↔ MCP)

| CLI | MCP tool | Purpose |
|-----|----------|---------|
| `init` | `init_project` | Scaffold `.agent-sim/` + gitignore |
| `guide` | `guide` | On-demand setup/ops guide (markdown) |
| `web` | `web` | Local report player (audio + transcript sync) |
| `preflight` | `preflight` | Config + LiveKit connectivity |
| `scenarios` | `list_scenarios` | List `scenarios/*.jsonl` |
| `plugins` | `list_plugins` | Verify plugins |
| `validate` | `validate_scenario` | Schema + lint |
| `export` | `export_scenario` | Parsed scenario JSON |
| `scenario-init` | `init_scenario` | Scaffold `.jsonl` with `//` guides + examples |
| `execute` | `execute_scenario` | Validate then run one JSONL scenario |
| `execute-all` | `execute_scenarios` | Batch (optional ids / tag) |
| `execute-dict` | `execute_scenario_dict` | Validate then run in-memory dict |
| `status` | `get_run_status` | SQLite run status |
| `log` | `get_run_log` | Filtered `events.jsonl` |
| `report` | `get_run_report` | Summary + verdict + audio path |
| `compare` | `compare_runs` | Diff two runs |
| `runs` | `list_runs` | Run history |

## Docs

- [AGENTS.md](AGENTS.md) — rules for AI agents (research loop, package boundary)
- [docs/smoke-test.md](docs/smoke-test.md) — first end-to-end run
- [docs/portability.md](docs/portability.md) — consumer-specific dispatch / observe setup
- [docs/plugins.md](docs/plugins.md) — verify plugins + Python API

## CI / Release

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [CI](.github/workflows/ci.yml) | PR / push → `main` | `pytest` (Python 3.10 + 3.12), `lk-sim --help`, `uv build` |
| [Release](.github/workflows/release.yml) | tag `v*` | test → build → GitHub Release (wheel + sdist); PyPI if `PYPI_API_TOKEN` secret is set |

Local check:

```bash
uv sync --extra dev
uv run pytest -q
uv build
```

Release:

```bash
git tag v0.1.0
git push origin v0.1.0
```
