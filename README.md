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

**One command — no manual uv/pipx/git setup.** The installer bootstraps
[uv](https://docs.astral.sh/uv/) if needed, then installs `lk-sim` from this repo
(git or GitHub source archive).

```bash
# macOS / Linux
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash

# Windows PowerShell
irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" | iex
```

Then:

```bash
lk-sim guide
lk-sim web --root /path/to/target   # no Node — report player is prebuilt into the package
```

Installer options (Unix) — auto-bootstraps uv; no PyPI / wheel:

```bash
# pin to a tag
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash -s -- --ref v0.1.0 --verify

# main + skip MCP auto-config
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash -s -- --ref main --no-mcp

# uninstall tool + MCP registrations
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash -s -- --uninstall
```

Windows PowerShell (full examples) — same zero-prereq path (bootstraps uv):

```powershell
# default = main
irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" | iex

# pin tag + verify
irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" -OutFile install.ps1
.\install.ps1 -GitRef v0.1.0 -Verify

# skip MCP / uninstall
.\install.ps1 -GitRef main -NoMcp
.\install.ps1 -Uninstall
```

By default the installer also registers the **MCP** server `livekit-agent-simulator`
as **`lk-sim mcp`** into common AI coding tools (Claude Code, Cursor, Cline,
Windsurf, VS Code Copilot, Gemini CLI, Amazon Q, OpenCode, Codex, Warp when present).

### Report player (maintainers)

Source: `web/` (Vite + TypeScript). **Users never run this** — CI builds into
`templates/report-player/` in the repo; install is from git, so the player is already there.

```bash
pnpm --dir web install
pnpm --dir web build    # → templates/report-player/ (served by lk-sim web)
pnpm --dir web dev      # HMR; proxy /api + /runs → lk-sim web on :8765
```

### Release (maintainers)

```bash
# after main is green (player assets committed or built on the tag job):
git tag v0.1.0 && git push origin main --tags
# → GitHub Actions: pnpm build → pytest → GitHub Release (install.sh + install.ps1 only)
# No PyPI / no wheel publish
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

## MCP (after install)

Installer writes the MCP command when tools are detected. Manual Cursor example:

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

Equivalent one-shot entry: `lk-sim-mcp` (same process as `lk-sim mcp`).

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
| [CI](.github/workflows/ci.yml) | PR / push → `main` | pnpm report-player build, `pytest` (3.10 + 3.12), `lk-sim --help` |
| [Release](.github/workflows/release.yml) | tag `v*` | pnpm build → pytest → GitHub Release (`install.sh` + `install.ps1` only; **no** PyPI / wheel) |

Local check:

```bash
uv sync --extra dev
pnpm --dir web build
uv run pytest -q
```

Release:

```bash
git tag v0.1.0
git push origin v0.1.0
```
