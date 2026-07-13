# Agent install guide — livekit-agent-simulator (`lk-sim`)

**Audience:** coding agents (Claude Code, Cursor, Codex, AmpCode, Windsurf, …) and humans who paste this URL into an agent.

**Goal:** install the `lk-sim` CLI on the user’s machine, scaffold `.agent-sim/` inside **the user’s project repo** (the LiveKit voice agent under test), fill config safely, register MCP if useful, and prove the setup with `preflight` (and optionally a smoke run).

**Hard rules for the agent:**

1. **Never import or edit the agent-under-test application source** (no patches to worker business code). Only create/edit files under `<target>/.agent-sim/` (gitignored).
2. **Never commit secrets.** `.agent-sim/` must stay gitignored. Do not paste API keys into git-tracked files.
3. **Do not invent LiveKit / Gemini credentials or dispatch metadata.** Discover from the target repo (§4.0) or ask the user. Read `.env` only with permission.
4. **Discover before `AskQuestion`.** Read target docs and existing `.agent-sim/` (read-only). Do not assume consumer-specific file paths or metadata keys (e.g. one repo’s `job-metadata.ts` or `customAgentId` is not universal).
5. Prefer **non-interactive** commands. Use `--root <absolute-path>` always.
6. Prefer the **portable installer** (no uv/pip on the user machine) unless the user is developing the simulator package itself.

---

## 0. What you are installing

| Piece | Purpose |
|-------|---------|
| CLI `lk-sim` | Black-box LiveKit room tester + report player |
| MCP server `livekit-agent-simulator` | Same ops for coding agents (`lk-sim mcp`) |
| Target folder `.agent-sim/` | Config, scenarios, reports, local cues/plugins (gitignored) |

The simulator joins LiveKit as `lk-sim-caller`, talks to the user’s already-running voice agent worker via Gemini Live, and writes forensic reports.

**Prerequisites the agent must verify or ask for:**

| Need | Why |
|------|-----|
| macOS / Linux / Windows | Portable packs ship for these |
| Network access | Download release + LiveKit + Gemini |
| A **running** LiveKit voice agent worker | Registered with a known `agent_name` |
| LiveKit Cloud (or self-host) URL + API key/secret | Room create + dispatch |
| Google API key with Gemini Live access | Simulated caller (+ optional judge) |

Optional: coding tools already installed (Claude Code / Cursor / …) so the installer can auto-register MCP.

---

## 1. Detect environment

Run these first and adapt.

macOS / Linux (bash):

```bash
uname -s
which lk-sim || true
lk-sim --help 2>/dev/null | head -5 || true
pwd
# If the user said "this project", use the current workspace root as TARGET_ROOT
```

Windows (PowerShell — note: plain `cmd.exe` has no `Set-Location`/`&&`; prefer PowerShell):

```powershell
Get-Command lk-sim -ErrorAction SilentlyContinue
lk-sim --help 2>$null | Select-Object -First 5
Get-Location
```

Set variables (agent should use absolute paths):

```bash
# TARGET_ROOT = the user's LiveKit agent / monorepo root (where .agent-sim will live)
TARGET_ROOT="$(pwd)"   # or the path the user named

# Optional pin (default installer = latest release)
# LK_SIM_REF=v0.1.0
```

```powershell
$TARGET_ROOT = (Get-Location).Path   # or the path the user named
# Optional pin: $env:LK_SIM_REF = "v0.1.0"
```

If `lk-sim --help` already works, skip §2 install and go to §3 init.

---

## 2. Install `lk-sim` (portable)

### macOS / Linux

```bash
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --verify
```

Pin a release (recommended for CI / reproducible agent setups):

```bash
curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
  | bash -s -- --ref v0.1.0 --verify
```

Flags:

| Flag | Meaning |
|------|---------|
| `--verify` | Run post-install check (`lk-sim --help`) |
| `--ref vX.Y.Z` / `--version` | Pin release tag (default: latest) |
| `--no-mcp` | Skip auto MCP registration |
| `--easy-mode` | Append install dir to shell PATH in rc files |
| `--uninstall` | Remove install |

Default binary location: `$HOME/.local/bin/lk-sim`  
If `lk-sim` is not found after install, ensure `~/.local/bin` is on `PATH` for this shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
hash -r 2>/dev/null || true
command -v lk-sim
lk-sim --help | head -20
```

### Windows PowerShell

```powershell
irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" -OutFile "$env:TEMP\lk-sim-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lk-sim-install.ps1" -Verify
# Optional pin:
# ... -File "$env:TEMP\lk-sim-install.ps1" -Ref v0.1.0 -Verify
```

`install.ps1` flags (differ slightly from `install.sh`):

| Flag | Meaning |
|------|---------|
| `-Verify` | Run post-install check (`lk-sim --help`) |
| `-Ref vX.Y.Z` / `-Version` | Pin release tag (default: latest) |
| `-NoMcp` | Skip auto MCP registration |
| `-Uninstall` | Remove install |
| `-Repair` | Fix broken nested layout (`current/lk-sim-windows-x64/`) without re-download |
| `-Quiet` | Suppress info logs |

Install locations: pack under `%LOCALAPPDATA%\lk-sim\current`, shim `lk-sim` under `%USERPROFILE%\.local\bin`.
The installer prepends that dir to the **user** `PATH` automatically — but already-open terminals do not see it.
If `lk-sim` is not found after install, open a **new** terminal, or for the current session:

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
Get-Command lk-sim
lk-sim --help | Select-Object -First 20
```

### Installer success criteria

- `command -v lk-sim` (bash) / `Get-Command lk-sim` (PowerShell) resolves
- `lk-sim --help` exits 0 and lists: `init`, `preflight`, `execute`, `scenario-from-run`, `web`, `mcp`, …
- Prefer not to use `uv run` / `pip install` for end users

### From source (only if user is developing the simulator package)

```bash
git clone https://github.com/quangdang46/livekit-agent-simulator.git
cd livekit-agent-simulator
uv sync --extra dev
uv run lk-sim --help
# When pointing at a target repo, still use absolute --root
uv run lk-sim init --root /abs/path/to/target
```

---

## 3. Init project scaffold in the user’s repo

```bash
lk-sim init --root "$TARGET_ROOT"
```

This creates (if missing):

```text
$TARGET_ROOT/.agent-sim/
  config.yaml          # secrets go here (gitignored)
  scenarios/smoke-hello.jsonl
  reports/
  plugins/example_verify.py
  cues/README.md
```

And ensures `.agent-sim/` is listed in `$TARGET_ROOT/.gitignore`.

**Idempotent:** re-running `init` does **not** overwrite existing `config.yaml`, scenarios, or reports — only creates missing scaffold files.

**Success criteria:**

```bash
test -f "$TARGET_ROOT/.agent-sim/config.yaml"
test -d "$TARGET_ROOT/.agent-sim/scenarios"
grep -q '\.agent-sim/' "$TARGET_ROOT/.gitignore" || true
lk-sim guide | head -5
```

---

## 4. Fill `.agent-sim/config.yaml` (required fields)

Open `$TARGET_ROOT/.agent-sim/config.yaml`. Required:

| Key | Source |
|-----|--------|
| `livekit.url` | `wss://…` LiveKit Cloud or self-host |
| `livekit.api_key` / `livekit.api_secret` | LiveKit server API credentials |
| `livekit.agent_name` | **Exact** dispatch name the worker registers (from worker docs, env, or LiveKit config — consumer-specific) |
| `simulator.google_api_key` | Gemini API key with Live model access |

Recommended defaults (already in template):

- `simulator.voice.model`: `gemini-3.1-flash-live-preview`
- `observe.record_audio: true` (stereo WAV L=sim R=agent)
- `observe.lk_agent_session: true` (automatic SDK tool/session events)
- `judge.model`: `gemini-2.5-flash` (only used if scenario has `PassCriteria`)

Optional but common:

```yaml
livekit:
  # Opaque JSON string for RoomAgentDispatch.metadata (consumer-specific keys).
  # lk-sim forwards this string as-is; discover keys from target docs/source (§4.0).
  # dispatch_metadata: '{"yourConsumerKey":"value"}'
  agent_join_timeout_ms: 25000

simulator:
  language: "en-US"   # or vi-VN, ja-JP, …
  voice:
    language: "en-US"

observe:
  timezone: "UTC"     # or Asia/Ho_Chi_Minh, …
  lk_agent_session: true

# Optional SIP defaults (mode is NEVER here — use scenario Caller.mode).
# Full guide: docs/telephony.md
# telephony:
#   outbound_trunk_id: "ST_xxxxxxxxxxxx"
#   dial_in: "+15551234567"              # inbound_sip: Gemini dials this
#   sim_inbound_number: "+15559876543"   # outbound_sip: Gemini answers this
#   prepare_ms: 3000
#   wait_until_answered: true
```

### Tool observability

LiveKit Agents SDK workers expose tool calls, outputs, state changes, errors, usage,
and final chat history through the standard `lk.agent.session` RemoteSession protocol.
It is enabled by default, so SDK workers do not need `tool_event_patterns`.

Set `observe.lk_agent_session: false` only for agents that do not implement this
protocol. For those agents, map custom data messages with
`observe.tool_event_patterns`; see [portability.md](../portability.md).

**How the agent should obtain values (order matters):**

1. **Discover** the target repo (§4.0) — docs, existing `.agent-sim/`, read-only code search for dispatch metadata and `agent_name`.
2. **Try env** (with user permission): read `.env` / `.env.local` in `TARGET_ROOT` and map common keys below.
3. **If still empty or ambiguous**, call **`AskQuestion`** (§4.1) — do not invent values. Paste secrets into `config.yaml` or read `.env`; never put API keys in `AskQuestion` option labels.
4. Never print full secrets in chat; confirm only that fields are **set**.

| Env var (common patterns in target repo) | `config.yaml` key |
|------------------------------------------|-------------------|
| `LIVEKIT_URL` | `livekit.url` |
| `LIVEKIT_API_KEY` | `livekit.api_key` |
| `LIVEKIT_API_SECRET` | `livekit.api_secret` |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY` | `simulator.google_api_key` |
| Consumer-specific (search docs — not one global name) | `livekit.agent_name` |
| `SIP_OUTBOUND_TRUNK_ID` (optional) | `telephony.outbound_trunk_id` |
| `SIP_INBOUND_TRUNK_ID` (optional) | `telephony.inbound_trunk_id` (docs/preflight) |

Per-scenario dispatch override (opaque JSON — keys are consumer-specific):

```jsonl
{"kind":"Dispatch","spec":{"metadata":"{\"yourConsumerKey\":\"value\"}"}}
```

More consumer wiring notes: [portability.md](../portability.md).

### 4.0 Discover consumer repo (before `AskQuestion`)

`lk-sim` is **target-agnostic**. Each consumer defines its own dispatch metadata keys, data topics, and docs layout. **Do not hardcode paths or field names from one repo** (e.g. `bootstrap/job-metadata.ts`, `customAgentId`) in this guide or in setup steps.

Read-only discovery in `TARGET_ROOT` (in order):

1. **Existing sim scaffold** — `.agent-sim/config.yaml`, `scenarios/*.jsonl` (especially `Dispatch` lines), `cues/`, `plugins/`.
2. **Docs** — `README*`, `docs/**` — search for: `dispatch`, `metadata`, `agent_name`, `RoomAgentDispatch`, `data_topics`.
3. **Env templates** — `.env.example`, `.env.local.example` — map only keys that exist there; do not assume a global env name for `agent_name` or metadata IDs.
4. **Code search (read-only, no edits)** — examples:
   - `dispatch_metadata`, `job.metadata`, `parseMetadata`, `RoomAgentDispatch`
   - `data_topics`, `publishData`, topic names the agent emits
   - `agent_name`, `agentName`, worker registration / dispatch name
5. **Portability** — [portability.md](../portability.md) for how lk-sim forwards opaque JSON and observes data topics.

**Infer wiring from discovery:**

| Finding | lk-sim action |
|---------|----------------|
| Worker reads JSON from dispatch/job metadata | Set `livekit.dispatch_metadata` and/or per-scenario `Dispatch.metadata` with the **same key names** found in docs/search (values from user or existing config — never invent) |
| Agent publishes named data topics | Set `observe.data_topics` to those topic strings (or `[]` to record all) |
| Non–LiveKit Agents SDK worker | Consider `observe.lk_agent_session: false` + `tool_event_patterns` (see portability) |
| Nothing requires opaque metadata | Leave `dispatch_metadata` unset |

**Ask the user only for gaps** discovery cannot resolve: which agent ID / metadata value, prod vs local dispatch name, permission to read `.env`, whether to run smoke `execute`.

### 4.1 `AskQuestion` tool (only after §4.0 discover + env)

When setup is blocked on a **user decision** discovery could not resolve, call the coding agent’s **`AskQuestion`** tool
(structured multiple-choice form). Available on **Cursor, Claude Code, Codex, Windsurf**, and similar hosts.
**Not** for typing secrets — use it for **which repo**, **credential source**, **toggles**, and **unresolved wiring**.

Rules:

- Run **§4.0 discover first** — do not ask “do you need dispatch metadata?” if docs/search already show required metadata keys.
- **≤ 3 questions per `AskQuestion` call** (batch related choices).
- Each question needs **≥ 2 options**; put the recommended default **first** and append `(Recommended)` to its label.
- User can always pick **Other** for a custom path, `agent_name`, metadata JSON, or consumer-specific keys.
- **Secrets** (`api_key`, `api_secret`, `google_api_key`): never list real values as options. Either read `.env` after user picks “read env”, or ask them to edit `config.yaml` / use **Other** for one-off paste.
- If the host has no `AskQuestion`, ask the same prompts in chat.

#### Turn 1 — typical `AskQuestion` (after `init` + §4.0, before writing config)

```json
{
  "title": "lk-sim setup",
  "questions": [
    {
      "id": "target_root",
      "prompt": "Where should .agent-sim/ live? (LiveKit worker repo, not dashboard)",
      "options": [
        {"id": "worker", "label": "Worker repo — path with LIVEKIT_* in .env (Recommended)"},
        {"id": "cwd", "label": "Current workspace folder"},
        {"id": "other", "label": "Other — I'll type the absolute path in Other"}
      ]
    },
    {
      "id": "credentials",
      "prompt": "How should I fill LiveKit URL, API key/secret, and Gemini key?",
      "options": [
        {"id": "read_env", "label": "Read from TARGET_ROOT/.env with my permission (Recommended if .env exists)"},
        {"id": "i_edit_yaml", "label": "I'll paste secrets into .agent-sim/config.yaml myself"},
        {"id": "other", "label": "Other — I'll provide values in Other / follow-up chat"}
      ]
    },
    {
      "id": "record_audio",
      "prompt": "Enable local call recording for lk-sim web replay?",
      "options": [
        {"id": "yes", "label": "Yes — observe.record_audio: true (Recommended)"},
        {"id": "no", "label": "No — skip conversation.wav"}
      ]
    }
  ]
}
```

**After answers:** write `config.yaml`. If `credentials=read_env`, map env vars (table above). If `i_edit_yaml`, stop and tell user which keys are still `YOUR_*` placeholders.

**Gemini key hint** (chat or comment in yaml — not an `AskQuestion` option):

> Create at [Google AI Studio](https://aistudio.google.com/apikey). Same key as worker `GOOGLE_API_KEY` is fine. Needs Gemini Live access for `gemini-3.1-flash-live-preview`.

#### Turn 2 — only if still ambiguous after discover (second `AskQuestion`, optional)

Skip questions already answered by §4.0 (e.g. skip dispatch-metadata question if discovery found required keys).

```json
{
  "title": "lk-sim setup (optional)",
  "questions": [
    {
      "id": "language",
      "prompt": "Sim caller language / locale?",
      "options": [
        {"id": "en-US", "label": "en-US (Recommended)"},
        {"id": "vi-VN", "label": "vi-VN"},
        {"id": "ja-JP", "label": "ja-JP"},
        {"id": "other", "label": "Other BCP-47 in Other"}
      ]
    },
    {
      "id": "dispatch_metadata",
      "prompt": "Where should opaque dispatch metadata live? (Only if discovery found required keys but not where to set them)",
      "options": [
        {"id": "none", "label": "Not needed / already in scenario JSONL (Recommended if discovery found nothing)"},
        {"id": "yes_config", "label": "Default for all runs — livekit.dispatch_metadata in config.yaml"},
        {"id": "yes_scenario", "label": "Per-scenario only — Dispatch line in JSONL"}
      ]
    },
    {
      "id": "run_smoke",
      "prompt": "Run smoke execute now?",
      "options": [
        {"id": "preflight_only", "label": "Stop after preflight — I'll start the worker myself (Recommended)"},
        {"id": "execute", "label": "Worker is already running — run execute smoke-hello"}
      ]
    }
  ]
}
```

If discovery found required metadata keys but not values, ask in chat or **Other** for the JSON string — **never invent** consumer IDs.

#### Reference — what each bucket maps to

**Order:** `TARGET_ROOT` → **discover (§4.0)** → credentials → toggles → optional wiring → preflight → confirm execute.

| Bucket | Use `AskQuestion` for | Maps to / action |
|--------|----------------------|------------------|
| **A. Target repo** | Which folder is `TARGET_ROOT` | `--root` on every command |
| **B. Credentials** | Read `.env` vs user edits yaml | `livekit.*`, `simulator.google_api_key` |
| **C. Toggles** | `record_audio`, language, timezone, judge | `observe.record_audio`, `simulator.language`, `observe.timezone`, `judge:` |
| **D. Product wiring** | Unresolved metadata keys/values, `data_topics`, `first_speaker` (after discover) | `dispatch_metadata`, `observe.data_topics`, scenario `Execute` / `Dispatch` |
| **E. Skip** | Worker start command, SIP, load test | Out of scope (§9) |
| **F. After preflight** | `run_smoke` question or plain chat | `execute` only if user chose it or confirmed worker is up |

Example yaml after **record_audio=yes** + **language=vi-VN**:

```yaml
simulator:
  language: "vi-VN"
  voice:
    language: "vi-VN"
observe:
  record_audio: true
  timezone: "Asia/Ho_Chi_Minh"
```

More on dispatch / data topics: [portability.md](../portability.md). Voice / cues / plugins after setup: `lk-sim guide`.

---

## 5. MCP registration (coding agents)

Default installer registers MCP server name **`livekit-agent-simulator`** → command `lk-sim mcp` into detected tools (Claude Code, Cursor, Cline, Windsurf, VS Code Copilot, Gemini CLI, Amazon Q, OpenCode, Codex, Warp).

If the user skipped MCP or tools were installed later, manual MCP config example:

```json
{
  "mcpServers": {
    "livekit-agent-simulator": {
      "command": "lk-sim",
      "args": ["mcp"]
    }
  }
}
```

Dev checkout (package not on PATH):

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

Every MCP tool needs `project_root` **except** `guide`. Prefer absolute `project_root`.

Typical MCP flow:

1. `guide`
2. `init_project` → `preflight`
3. `list_scenarios` / `init_scenario` / `validate_scenario`
4. `execute_scenario` (or `execute_scenarios` with `repeat` / `pass_at_k`)
5. `get_run_report` / `get_run_log` / `web`
6. On failure: `scenario_from_run` → review draft → re-run

---

## 6. Preflight (must pass before promising a full call)

```bash
lk-sim preflight --root "$TARGET_ROOT"
# offline config-only:
lk-sim preflight --no-connectivity --root "$TARGET_ROOT"
```

**Success:** JSON `ok: true` with checks for config, livekit.url, folders, google key, and (if connectivity on) `livekit.api` list_rooms.

Common failures:

| Symptom | Fix |
|---------|-----|
| config missing | `lk-sim init --root …` first |
| livekit.api 401 | Wrong URL / api_key / api_secret |
| agent timeout later | Worker not running or `agent_name` mismatch |
| Windows: `No module named 'encodings'` / `Could not find platform independent libraries` | Broken portable layout from older installer — run `install.ps1 -Repair -Verify` or reinstall with latest `install.ps1` |

---

## 7. Scenarios and first execute (optional but recommended)

List / scaffold:

```bash
lk-sim scenarios --root "$TARGET_ROOT"
lk-sim scenario-init my-case --root "$TARGET_ROOT"   # // guide lines in JSONL
lk-sim validate smoke-hello --root "$TARGET_ROOT"
```

**Worker must be running** and registered with the same `livekit.agent_name` before execute.

```bash
lk-sim execute smoke-hello --root "$TARGET_ROOT"
# flake control:
lk-sim execute smoke-hello --root "$TARGET_ROOT" --repeat 3 --pass-at-k 2
# suite:
lk-sim execute-all --tag smoke --root "$TARGET_ROOT"
```

Inspect:

```bash
lk-sim runs --root "$TARGET_ROOT"
lk-sim report <run-id> --root "$TARGET_ROOT"
lk-sim log <run-id> --kind "transcript.*" --root "$TARGET_ROOT"
lk-sim web --root "$TARGET_ROOT"    # http://127.0.0.1:8765 — Ctrl+C to stop
```

Promote a failure to a draft regression case:

```bash
lk-sim scenario-from-run <run-id> --root "$TARGET_ROOT"           # dry-run
lk-sim scenario-from-run <run-id> --root "$TARGET_ROOT" --write  # write JSONL
# then human/agent reviews Persona + Assert before treating as golden
```

Assert highlights (for scenario authors after setup):

- `latency` — hard gate on turn p95 / TTFW / barge recovery rate  
- `recovery` — agent re-engages after barge  
- `ended_by` — `sim` | `agent` | `detect` after script `hang_up` or natural end  

Script action `hang_up` makes the sim caller leave the room (hard hangup).

### Telephony scenarios (optional)

Package templates (copy into `.agent-sim/scenarios/`):

- `outbound-callee-sim.jsonl` — Gemini answers (`Caller.mode: outbound_sip`)
- `inbound-caller-sim.jsonl` — Gemini dials agent DID (`Caller.mode: inbound_sip`)

```bash
# After telephony: block in config.yaml (trunk + dial_in / sim_inbound_number):
lk-sim validate inbound-caller-sim --root "$TARGET_ROOT"
# lk-sim execute inbound-caller-sim --root "$TARGET_ROOT"   # needs real trunk + DID routing
```

Mode is **only** in scenario `Caller` — never in `config.yaml`.  
Guide: https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/telephony.md  
Ops detail: `lk-sim guide` (templates/GUIDE.md).

SIP asserts: `Assert.spec.sip.participant_present` / `dial_answered` / `call_status_any`.

---

## 8. Definition of done (agent checklist)

Mark setup complete only when **all** of these are true:

- [ ] `lk-sim --help` works on PATH
- [ ] `$TARGET_ROOT/.agent-sim/config.yaml` exists with LiveKit + `agent_name` + Gemini key set
- [ ] `.agent-sim/` is gitignored
- [ ] `lk-sim preflight --root "$TARGET_ROOT"` → `ok: true`
- [ ] User knows the worker must be running before `execute`
- [ ] Consumer dispatch metadata / `data_topics` set when discovery (§4.0) shows they are required
- [ ] (Tool scenarios) report contains `tool.*` and `session.chat_history`, with no `tool_events` observe gap
- [ ] (Optional) MCP `livekit-agent-simulator` registered if they use a coding agent
- [ ] (Optional) `lk-sim execute smoke-hello --root "$TARGET_ROOT"` → `status: done` or a clear next fix (agent timeout / Gemini quota)
- [ ] (Optional SIP) `telephony:` trunk/DID filled when testing `inbound_sip` / `outbound_sip`; scenarios validated

**Do not claim “fully working E2E”** if preflight failed or the worker is not registered.

---

## 9. Safety / scope boundaries

- **In scope:** install CLI, scaffold `.agent-sim/`, fill config from user/env, preflight, create/edit scenarios under `.agent-sim/scenarios/`, run executes, reports, MCP config.
- **Out of scope:** rewriting the user’s agent product logic; load testing; provisioning carrier trunks/DIDs; committing secrets; changing production LiveKit keys without confirmation.
- **In package (optional):** SIP **scenario modes** (`inbound_sip` / `outbound_sip`) via SimLeg — see [telephony.md](../telephony.md). Target still owns trunk IDs and numbers in gitignored config.
- **Quota:** Gemini free tier can 429 on judge/Live after many runs — report that honestly; hard gates (status/assert/script) still work when judge is soft-error.

---

## 10. One-shot command sequence (copy for agents)

### macOS / Linux (bash)

```bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
TARGET_ROOT="$(pwd)"   # change if needed

# 1) Install CLI (skip if already present)
if ! command -v lk-sim >/dev/null 2>&1; then
  curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" \
    | bash -s -- --verify
  export PATH="$HOME/.local/bin:$PATH"
  hash -r 2>/dev/null || true
fi
lk-sim --help >/dev/null

# 2) Scaffold target project
lk-sim init --root "$TARGET_ROOT"

# 3) STOP: discover TARGET_ROOT (§4.0), then fill $TARGET_ROOT/.agent-sim/config.yaml
#    livekit.url / api_key / api_secret / agent_name
#    simulator.google_api_key
#    optional: livekit.dispatch_metadata, observe.data_topics (from consumer docs/search)
#    Then continue:

lk-sim preflight --root "$TARGET_ROOT"
# 4) Ensure voice agent worker is running with matching agent_name
# lk-sim execute smoke-hello --root "$TARGET_ROOT"
# lk-sim web --root "$TARGET_ROOT"
```

### Windows (PowerShell)

```powershell
$ErrorActionPreference = "Stop"
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
$TARGET_ROOT = (Get-Location).Path   # change if needed

# 1) Install CLI (skip if already present)
if (-not (Get-Command lk-sim -ErrorAction SilentlyContinue)) {
  irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" -OutFile "$env:TEMP\lk-sim-install.ps1"
  powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lk-sim-install.ps1" -Verify
  $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}
lk-sim --help | Out-Null

# 2) Scaffold target project
lk-sim init --root $TARGET_ROOT

# 3) STOP: discover TARGET_ROOT (§4.0), then fill $TARGET_ROOT\.agent-sim\config.yaml
#    livekit.url / api_key / api_secret / agent_name
#    simulator.google_api_key
#    optional: livekit.dispatch_metadata, observe.data_topics (from consumer docs/search)
#    Then continue:

lk-sim preflight --root $TARGET_ROOT
# 4) Ensure voice agent worker is running with matching agent_name
# lk-sim execute smoke-hello --root $TARGET_ROOT
# lk-sim web --root $TARGET_ROOT
```

---

## 11. Links

| Resource | URL |
|----------|-----|
| Repo | https://github.com/quangdang46/livekit-agent-simulator |
| Installer (macOS/Linux) | https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh |
| Installer (Windows) | https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1 |
| This guide (raw) | https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/docs/guide/installation.md |
| Ops guide | package `lk-sim guide` or `templates/GUIDE.md` (voice, cues, plugins) |
| Portability | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/portability.md |
| Telephony (SIP modes) | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/telephony.md |
| Smoke notes | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/smoke-test.md |
| L3 observer design | `docs/plans/PLAN-20260713-lk-agent-session-observer.md` |

When instructions conflict: **this file + `lk-sim guide`** beat outdated blog snippets. Prefer latest release unless the user pins a tag.
