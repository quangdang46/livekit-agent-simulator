# livekit-agent-simulator — setup & ops guide

For coding agents and humans. **CLI (`lk-sim`) and MCP share the same ops.**

Package dials **any** LiveKit voice agent with a Gemini Live simulated caller and writes
a forensic report. The agent under test is a **black box** — never import or edit target
application code unless the user asks.

Target-only data lives under `<target>/.agent-sim/` (config, scenarios, reports, plugins).

---

## 0. If you know nothing — do this order

1. `guide` (this text) — read once.
2. Confirm the **voice agent worker is running** and registered on LiveKit with a known `agent_name`.
3. `init` on the target repo → fill credentials in `.agent-sim/config.yaml`.
4. `preflight` until `ok: true`.
5. `scenario-init <id>` → edit the JSONL (`//` lines are guides; delete unused kinds).
6. `validate <id>` then `execute <id>`.
7. `report <run-id>` and/or **`web`** (browser: play audio + highlight transcript).

```bash
# Install once (optional): curl install.sh | bash  →  lk-sim on PATH
# From anywhere; point --root at the target repo under test
lk-sim guide
lk-sim init --root /path/to/target
# edit /path/to/target/.agent-sim/config.yaml
lk-sim preflight --root /path/to/target
lk-sim scenario-init smoke-hello --root /path/to/target   # skip if file already exists
lk-sim validate smoke-hello --root /path/to/target
lk-sim execute smoke-hello --root /path/to/target
lk-sim report <run-id> --root /path/to/target
lk-sim web --root /path/to/target                         # Ctrl+C to stop server
```

MCP: same names as the right-hand column in §3 (`guide`, `init_project`, `preflight`, …).
Every MCP tool needs `project_root` **except** `guide`.

---

## 1. Config (`.agent-sim/config.yaml`)

Created by `init`. **Gitignored.** Paste secrets here (no env substitution in v1).

| Section | Required | Purpose |
|---------|----------|---------|
| `livekit.url` | yes | `wss://…` LiveKit Cloud or self-host |
| `livekit.api_key` / `api_secret` | yes | Server API credentials |
| `livekit.agent_name` | yes | Must match the worker’s registered dispatch name |
| `livekit.dispatch_metadata` | no | Default opaque JSON **string** for all runs |
| `livekit.agent_join_timeout_ms` | no | Default 25000 |
| `simulator.google_api_key` | yes | Gemini API key for sim caller (+ judge) |
| `simulator.voice.model` / `voice` / `language` | no | Defaults: flash-live model, Puck, `en-US` |
| `judge.model` | no | If present + scenario PassCriteria → post-run LLM judge |
| `observe.record_audio` | no | `true` → local stereo WAV (L=sim, R=agent), no Egress |
| `observe.timezone` | no | Default `UTC` (report timestamps) |
| `observe.data_topics` | no | Empty = all data topics; else filter |
| `observe.tool_event_patterns` | no | Map data payloads → `tool.start` / `tool.end` / `tool.error` |

**Opaque dispatch:** `dispatch_metadata` and scenario `Dispatch.spec.metadata` are passed through
as JSON strings. Core **never** parses consumer keys (e.g. product agent ids).

---

## 2. Scenarios

### Create

```bash
lk-sim scenario-init my-case --root /path/to/target
# → .agent-sim/scenarios/my-case.jsonl
```

- Full-line `// …` comments document each kind and important fields.
- Runtime **ignores** lines starting with `//`.
- Delete an entire kind by removing its `//` block **and** the following JSON line.
- Do not use `#` comments inside JSON objects.

### Kinds (header order)

| Kind | Required? | Role |
|------|-----------|------|
| `Scenario` | yes | `metadata.id`, `locale`, `tags` |
| `Persona` | yes | Sim caller; `spec.brief` required |
| `Context` | no | Extra notes for persona |
| `Simulator` | no | Defaults; overridden by Execute |
| `Execute` | recommended | `max_turns`, `timeout_s`, `first_speaker` |
| `Dispatch` | no | Per-scenario opaque metadata JSON string |
| `Script` | no | Timed cues (`agent_speaking`); `gemini_text` \| `room_pcm` |
| `Plugins` | no | Load `.agent-sim/plugins/*.py` |
| `PassCriteria` | no | Judge rubric strings |

### Run

```bash
lk-sim validate my-case --root /path/to/target
lk-sim execute my-case --root /path/to/target
lk-sim execute-all --tag smoke --root /path/to/target
# In-memory (CI / agents): MCP execute_scenario_dict or CLI execute-dict -f file.json
```

`first_speaker`:

- `user` — sim speaks first (agents that wait for caller audio).
- `agent` — agent greets first (sim is nudged after agent speech if no Script).

---

## 3. Public ops (CLI ↔ MCP)

| CLI | MCP tool |
|-----|----------|
| `guide` | `guide` |
| `web` | `web` |
| `init` | `init_project` |
| `preflight` | `preflight` |
| `scenarios` | `list_scenarios` |
| `plugins` | `list_plugins` |
| `validate` | `validate_scenario` |
| `export` | `export_scenario` |
| `scenario-init` | `init_scenario` |
| `execute` | `execute_scenario` |
| `execute-all` | `execute_scenarios` |
| `execute-dict` | `execute_scenario_dict` |
| `status` | `get_run_status` |
| `log` | `get_run_log` |
| `report` | `get_run_report` |
| `compare` | `compare_runs` |
| `runs` | `list_runs` |

There is **no** separate `run` command — always validate-then-run via `execute*`.

---

## 4. Reports & web player

Directory: `.agent-sim/reports/<run-id>/`

`run_id` = `{scenario_id}-{YYYYMMDD-HHMMSS}-{hex4}` (UTC), e.g. `smoke-hello-20260711-103045-a3f2`.

| File | Contents |
|------|----------|
| `events.jsonl` | Canonical event stream |
| `timeline.md` | Human narrative table |
| `summary.json` | Duration, turns, judge verdict |
| `meta.json` | Scenario, room, config snapshot (no secrets) |
| `conversation.wav` | Stereo PCM if `observe.record_audio: true` |
| `cues.json` | Built on demand by `web` for transcript↔audio sync |

```bash
lk-sim report <run-id> --root /path/to/target
lk-sim log <run-id> --kind "transcript.*" --root /path/to/target
lk-sim runs --root /path/to/target
lk-sim web --root /path/to/target              # newest run
lk-sim web <run-id> --root /path/to/target     # specific run
# Opens http://127.0.0.1:8765 — play audio; transcript highlights by time; click line to seek
```

No Node/Vite on the user machine. Player assets ship inside the package.

---

## 5. Common failures

| Symptom | What to check |
|---------|----------------|
| `preflight` config fail | Missing `.agent-sim/config.yaml` → `init` first |
| `livekit.api` fail | `url` / `api_key` / `api_secret` |
| `dispatch.agent_timeout` | Worker process up? `agent_name` exact match (e.g. `…-local` vs prod)? |
| Agent joins but silent | `Execute.first_speaker`; worker may need `Dispatch.metadata` |
| Sim never speaks after agent | Normal nudge only when `first_speaker=agent` and no Script |
| Judge skipped | Need `PassCriteria` **and** `judge:` block in config |
| No `conversation.wav` | Set `observe.record_audio: true` |
| Scenario JSON error | Remove `#` comments; only full-line `//` allowed |

---

## 6. Rules for coding agents

- **Generic core:** do not hardcode consumer languages, agent IDs, data topics, or business rules in package `src/`.
- **Customize in target** `.agent-sim/` only (config, scenarios, plugins).
- **No legacy shims** in this package while pre-1.0 — one clear flag name.
- Prefer `execute` / `execute_scenario` / `execute_scenario_dict` over custom Python runners.
- Deep package rules + research loop: read package `AGENTS.md`.
- Consumer wiring examples only: `docs/portability.md` (load when setting up a target, not for core bugs).
