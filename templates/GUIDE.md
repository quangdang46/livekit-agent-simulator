# livekit-agent-simulator — setup & ops guide

For coding agents and humans. **CLI (`lks`, alias: `lk-sim`) and MCP share the same ops.**

Package dials **any** LiveKit voice agent with a Gemini Live simulated caller and writes
a forensic report. The agent under test is a **black box** — never import or edit target
application code unless the user asks.

Target-only data lives under `<target>/.agent-sim/` (config, scenarios, reports, plugins).

**Related docs** (canonical URLs — `docs/` is **not** shipped in the installed wheel; agents should fetch these links, not repo-relative paths):

| Topic | URL |
|-------|-----|
| First-time install + `init` + preflight | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/guide/installation.md |
| Verify plugins (full API) | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/plugins.md |
| Consumer dispatch / data topics / tool patterns | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/portability.md |
| Caller barge / silence / hang-up patterns | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/caller-pattern-plan.md |
| Package rules for coding agents | https://github.com/quangdang46/livekit-agent-simulator/blob/main/AGENTS.md |
| Telephony WebRTC / inbound / outbound SIP | https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/telephony.md |
| WAV cue format | https://github.com/quangdang46/livekit-agent-simulator/blob/main/templates/cues/README.md |

---

## 0. If you know nothing — do this order

1. `guide` (this text) — read once.
2. Confirm the **voice agent worker is running** and registered on LiveKit with a known `agent_name`.
3. `init` on the target repo → fill credentials in `.agent-sim/config.yaml`.
4. `preflight` until `ok: true`.
5. `scenario-init <id>` → edit the JSONL (`//` lines are guides; delete unused kinds).
6. `validate <id>` then `execute <id>` (optional ``--name``, ``--repeat N --pass-at-k K``).
7. `report <run-id>` and/or **`web`** (browser: play audio + highlight transcript; list auto-updates).
8. If a run fails, promote it to a permanent test: ``scenario-from-run <run-id> --write``, review, add to suite.

```bash
# Install once (optional) — full steps: https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/guide/installation.md
# curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash
# From anywhere; point --root at the LiveKit agent repo (not the dashboard)
lks guide
lks init --root /path/to/target   # safe to re-run; does not overwrite existing config/scenarios
# edit /path/to/target/.agent-sim/config.yaml
lks preflight --root /path/to/target
lks scenario-init smoke-hello --root /path/to/target   # skip if file already exists
lks validate smoke-hello --root /path/to/target
lks execute smoke-hello --root /path/to/target         # → reports/001-smoke-hello-YYYYMMDD-HHMMSS-xxxx/
lks report 001-smoke-hello-… --root /path/to/target
lks web --root /path/to/target                         # Ctrl+C to stop server
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
| `judge.model` / `base_url` / `api_key` | no | Soft LLM judge; HTTP OpenAI chat when `base_url` set, else Gemini |

| `observe.record_audio` | no | `true` → local stereo WAV (L=sim, R=agent), no Egress |
| `observe.timezone` | no | Default `UTC` (report timestamps) |
| `observe.lk_agent_session` | no | Default `true`; SDK tools, state, errors, usage + final chat history via `lk.agent.session` |
| `observe.data_topics` | no | Empty = all data topics; else filter |
| `observe.tool_event_patterns` | no | Fallback for non-SDK custom data payloads → `tool.start` / `tool.end` / `tool.error` |
| `telephony.*` | no | Optional SIP defaults (`outbound_trunk_id`, `dial_in`, `sim_inbound_number`, …). **Mode is never here** — use scenario `Caller.mode`. See `docs/telephony.md`. |

**Opaque dispatch:** `dispatch_metadata` and scenario `Dispatch.spec.metadata` are passed through
as JSON strings. Core **never** parses consumer keys (e.g. product agent ids). If the agent
bootstraps from dispatch metadata, set `livekit.dispatch_metadata` or
per-scenario `Dispatch` — see https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/portability.md .

**Telephony modes** (scenario only): `webrtc_sim` (default) · `inbound_sip` · `outbound_human_pickup` · `outbound_sim_callee` · `agent_dials`.
Templates: `outbound-human-pickup.jsonl`, `outbound-callee-sim.jsonl`, `inbound-caller-sim.jsonl`. Full guide: `docs/telephony.md`.

### Voice, language & call recording

The sim caller is **always voice** when `simulator.google_api_key` is set (Gemini Live TTS in the room).
There is no separate “enable voice” toggle — only **which voice/language** and **whether to save WAV**.

| Layer | What it does | Where to set |
|-------|----------------|--------------|
| **Sim speech** | Gemini Live speaks as the caller | `simulator.voice.*`, `simulator.language` |
| **Persona locale** | Prompt + scenario language hint | `Scenario.metadata.locale`, optional `Persona.spec.language` |
| **Vocal barge / backchannel** | Real speech WAV into sim mic (STT hears it) | Script `delivery: room_pcm` + `asset: voice.*` or `.agent-sim/cues/*.wav` |
| **Call recording** | Stereo `conversation.wav` for replay (`lks web`) | `observe.record_audio: true` |
| **Soft judge** | Post-run rubric (not a hard CI gate by default) | `judge:` in config **and** scenario `PassCriteria` |

Example — Vietnamese caller + local recording (template defaults are `en-US` / `UTC`):

```yaml
simulator:
  google_api_key: "…"
  language: "vi-VN"
  voice:
    model: "gemini-3.1-flash-live-preview"
    voice: "Puck"          # any Gemini Live voice name your key supports
    language: "vi-VN"

judge:
  # Gemini (default when base_url omitted) — uses simulator.google_api_key
  model: "gemini-2.5-flash"
  temperature: 0
  # Or HTTP via OpenAI-compatible gateway. `endpoint_type` = wire format (default openai):
  # base_url: "http://localhost:8080/v1"
  # api_key: "sk-..."
  # model: "gpt-4o-mini"
  # endpoint_type: openai   # openai → /chat/completions | anthropic → /messages

observe:
  record_audio: true     # reports/<run-id>/conversation.wav — L=sim, R=agent
  timezone: "Asia/Ho_Chi_Minh"
  lk_transcription: true
  lk_agent_session: true # default; automatic for LiveKit Agents SDK workers
  # Optional fallback for non-SDK custom events — see portability.md:
  # https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/portability.md
  # data_topics: ["myapp.flow"]
  # tool_event_patterns: []
```

Per-scenario language override in JSONL: `"metadata":{"locale":"vi-VN"}` and/or
`"Persona":{"spec":{"language":"vi-VN",…}}`.

### Cue library aliases (optional)

Drop custom WAVs in `.agent-sim/cues/` or add search paths / short names in config:

```yaml
cues:
  dirs:
    - media/noise          # relative to project root
  aliases:
    office: office_loop.wav
```

Scenario: `"asset":"office","delivery":"room_pcm"`. List built-ins + overrides: `lks cues --root .`

---

## 2. Scenarios

### Create

```bash
lks scenario-init my-case --root /path/to/target
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
| `Persona` | yes | Character: `brief`, `goals`, `traits`, **`constraints`**, **`speech_conditions`** |
| `Context` | no | `notes` + optional opaque `fixtures` hints |
| `Simulator` | no | Defaults; overridden by Execute |
| `Execute` | recommended | `max_turns`, `timeout_s`, `first_speaker` |
| `Dispatch` | no | Per-scenario opaque metadata JSON string |
| `Caller` | no | Transport mode: `webrtc_sim` (default) · `inbound_sip` · `outbound_human_pickup` · `outbound_sim_callee` · `agent_dials` |
| `Telephony` | no if WebRTC | SIP dial params: `call_to` / `dial_in` / `sip_trunk_id` / `prepare_ms` (overrides config) |
| `Behavior` | no | Hamming policy → auto Script (`barge_ins`, `user_silence`, `ambient`, `hang_ups`) |
| `Script` | no | Timed cues (`speak`, `wait`, **`hang_up`**) (wins over Behavior on same step `id`) |
| `Assert` | no | tools / transcript / **`sip`** / **`tool_order`** / outcomes (`transcript_contains`, **`recovery`**, **`latency`**, **`ended_by`**, **`goals_met`**, **`constraint_respected`**, `llm_bool`) |
| `Plugins` | no | Load local verify modules — see **Verify plugins** below |
| `PassCriteria` | no | Soft LLM judge rubric — flat `criteria[]` **or** `judges[]` + `mode` (`all` \| `majority` \| `any`) |

### Caller character (Hamming-aligned)

- Persona prompt now uses a **numbered GOAL checklist** (`## YOUR GOALS`) plus **guardrails** (`## GUARDRAILS`)
  - Caller must work through all goals one-by-one; early `[END_CALL]` causes a failed test
  - External verification via `Assert.spec.outcomes[].type: goals_met`
- **constraints[]** → hard rules in Gemini system prompt  
- **speech_conditions** → auto barge / ambient / silence Script if you skip hand-written Script  
  - `barge_policy: mid_agent_turn` + optional `barge_asset: builtin:voice.barge_short` (speech WAV; `with_blip` defaults off for `voice.*`)  
  - `noise_gain` / `barge_gain` (`0.0`–`1.0`) scale auto-compiled ambient / barge cues (also per-step Script `gain` / `volume`)
  - **Continuous ambient bed:** `noise_when: "background"` (or Behavior/Script `"loop": true`) re-queues `room_pcm` noise until hang-up (parallel under speech). One-shot bursts stay default (`once` / no loop).  
- **Behavior** kind → explicit barge/silence/ambient policies; set Script step `class` (`correction` \| `backchannel` \| `noise` \| `dtmf` \| `silence` \| `escalate`) so recovery metrics and web chips stay honest  
- **Assert** `outcomes` type **`recovery`** → agent re-engages after barge (`min_agent_finals_after_barge_in`, optional `max_ms_after_barge_to_agent_final`)
- **Assert** `outcomes` type **`latency`** → hard CI gates on turn p50/p95, TTFW, recovery percentiles, barge recovery rate
  - Example: `{"id":"speed","type":"latency","max_turn_p95_ms":3500,"max_ttfw_ms":5000,"require_turn_samples":1}`
- **Assert** `outcomes` type **`ended_by`** → assert which side ended the call (`sim` / `agent` / `detect`)
  - Example: `{"id":"caller_hung_up","type":"ended_by","who":"sim"}`
- **Assert** `outcomes` type **`goals_met`** → LLM judge verifies the simulated caller stated/pursued at least N persona goals before `[END_CALL]` (hard fail if not met)
  - Example: `{"id":"goals_done","type":"goals_met","min_goals":2,"goals":["Hear greeting","Ask about pricing"]}`
  - Without explicit `goals`, falls back to `Persona.spec.goals` from the scenario
- **Assert** `outcomes` type **`constraint_respected`** → hard fail if **caller** transcript contains forbidden phrases/patterns (`must_not_phrases` and/or `must_not_match`)
  - Example: `{"id":"no_card","type":"constraint_respected","must_not_phrases":["4111","CVV"]}`
- **Assert** `tool_order` → required subsequence of `tool.start` names (not necessarily contiguous)
  - Example: `{"kind":"Assert","spec":{"tool_order":["lookup","book"]}}`
- **Script action `hang_up`** → sim caller disconnects from room (cúp máy thật)
  - Example: `{"id":"hangup","action":"hang_up","trigger":"time","delay_ms":5000,"say":"Thôi em cúp đây"}`  
- See https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/caller-pattern-plan.md and `templates/examples/character-impatient.jsonl` (shipped in package after `init`)

### PassCriteria (soft judge)

Flat list (backward compatible):

```jsonl
{"kind":"PassCriteria","spec":{"criteria":["The agent answered the caller's question"]}}
```

Multi-judge groups (aggregate with `mode`):

```jsonl
{"kind":"PassCriteria","spec":{"mode":"majority","judges":[{"id":"task","criteria":["Task completed"]},{"id":"tone","criteria":["Polite tone"]}]}}
```

Builtin dimensions (Hamming/LiveKit-shaped — `evals.presets`):

```jsonl
{"kind":"PassCriteria","spec":{"judges":[{"id":"tc","builtin":"task_completion"},{"id":"acc","builtin":"accuracy"}]}}
```

Or flat: `"criteria":["builtin:task_completion"]`.

Judge verdicts: `pass` | `fail` | `maybe` (soft unless `--strict-judge`). Criteria may mark `relevant=false` to skip irrelevant checks.

`mode`: `all` (default) \| `majority` \| `any`. Still soft unless you pass `--strict-judge`. Needs `judge:` in config (Gemini or HTTP `base_url`/`api_key`).

### Audio cues (built-in + per-repo custom)

| Source | Location | Scenario `asset` |
|--------|----------|------------------|
| Built-in noise | package `templates/cues/` | `builtin:noise.loud`, `@noise.ambient`, `noise.blip` |
| Built-in **speech** | same | `builtin:voice.barge_short`, `voice.barge_sorry`, `voice.backchannel`, `voice.barge_vi` |
| Target override | `.agent-sim/cues/*.wav` | `my_cafe.wav` (same name **overrides** built-in) |
| Aliases / dirs | `config.yaml` → `cues:` | short name from `cues.aliases` |

```bash
lks cues --root /path/to/target
lks cues --root /path/to/target --resolve builtin:voice.barge_short
# MCP: list_cues(project_root=…)
```

WAV: **PCM16 mono @ 24 kHz**. Prefer `voice.*` for audible barge-in; noise for beds/bursts. Details: https://github.com/quangdang46/livekit-agent-simulator/blob/main/templates/cues/README.md

### Verify plugins (custom Script checks)

Extend `Script.verify` with project-specific checks on `events.jsonl` — no fork of the sim package.
Full API: https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/plugins.md

**1. Copy and edit the example**

```bash
cp templates/plugins/example_verify.py /path/to/target/.agent-sim/plugins/my_checks.py
# or copy from the package after init: .agent-sim/plugins/example_verify.py
```

**2. Register the module in scenario JSONL**

```jsonl
{"kind":"Plugins","spec":{"modules":["my_checks"]}}
```

**3. Wire plugins on Script verify** (built-in checks still run first)

```jsonl
{"kind":"Script","spec":{"steps":[{"id":"bc","trigger":"agent_speaking","delay_ms":900,"say":"uh-huh","delivery":"gemini_text"}],"verify":{"require_during_agent_speech":true,"min_agent_finals_after_first_cue":1,"plugins":["example_backchannel_continue"],"plugin_options":{"example_backchannel_continue":{"min_agent_finals":1}}}}}
```

Discover loaded plugins:

```bash
lks plugins --root /path/to/target
# MCP: list_plugins(project_root=…)
```

Ship plugins from an installable package via `[project.entry-points."lk_sim.plugins"]` — see https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/plugins.md

### Run

```bash
lks validate my-case --root /path/to/target
lks execute my-case --root /path/to/target                    # → reports/001-my-case/
lks execute my-case --name demo --root /path/to/target        # → reports/002-demo/
lks execute my-case --repeat 5 --pass-at-k 3   # pass@k flake control
lks execute-all --tag smoke --root /path/to/target
lks execute-all --tag smoke --repeat 3 --pass-at-k 2   # flake in batch
lks execute-all --tag smoke --parallel 3 --root /path/to/target  # up to 3 scenarios at once
# In-memory (CI / agents): MCP execute_scenario_dict or CLI execute-dict -f file.json [--name]
```

**Run folder name (`--name`):** each execute gets an auto sequence prefix from existing
folders under `.agent-sim/reports/` (`001`, `002`, …). Default slug is the scenario id;
``--name`` replaces that slug only (scenario id stays in `meta.json`).

| Command | Report dir |
|---------|------------|
| `execute sp-vad-rt-barge-early` | `001-sp-vad-rt-barge-early/` |
| `execute sp-vad-rt-barge-early --name demo` | `001-demo/` (or next free seq) |

MCP: `execute_scenario(..., run_name="demo")` / `execute_scenario_dict(..., run_name=…)`.

**Flake control (pass@k):** run the same scenario N times; CI gate (hard: status / assert / script)
passes only if ≥ K iterations are green. Judge verdicts remain soft unless ``--strict-judge``.
Useful for barge / latency / noise scenarios where Gemini behavior varies.
Each iteration still allocates its own ``{NNN}-…`` folder.

```bash
lks execute my-barge-case --repeat 7 --pass-at-k 5 --root /path/to/target
```

### Promote a failed run to a permanent test (fail → golden)

```bash
lks scenario-from-run <run-id> --root /path/to/target
# dry-run: prints draft JSONL. Review Persona + Assert.
lks scenario-from-run <run-id> --root /path/to/target --write
# writes .agent-sim/scenarios/from-<source>-<id>.jsonl
# Then edit, validate, add to execute-all
lks validate from-my-source-xxxx --root /path/to/target
```

The draft recovers Persona, Dispatch, Execute spec, and run stats from the source report.
It adds a basic transcript Assert + recovery Assert (when barges present).
``Context.notes`` carries the source run_id, judge info, and metric hints.
**Review before promoting** — the draft is a starting point, not a golden assertion.

`first_speaker`:

- `user` — sim speaks first (agents that wait for caller audio).
- `agent` — agent greets first (sim is nudged after agent speech if no Script).


### Telephony (inbound / outbound SIP)

Mode is **per scenario** (`Caller.mode`), never in `config.yaml`.

| Mode | Gemini role | Config / scenario needs |
|------|-------------|-------------------------|
| `webrtc_sim` | Caller (default) | No `telephony:` required |
| `inbound_sip` | Caller dials agent DID | `telephony.outbound_trunk_id` + `dial_in` (or `Telephony.dial_in`) |
| `outbound_human_pickup` | Human answers; Gemini colocated | trunk + `call_to` (handset E.164); optional `handset_isolation` |
| `outbound_sim_callee` | Gemini SIP callee (hairpin) | trunk + `call_to` / `sim_inbound_number` that routes into sim-room |
| `agent_dials` | Callee; agent dials | Cooperative agent + sim answer path |

Package templates (copy into target `.agent-sim/scenarios/`):

- `templates/outbound-human-pickup.jsonl`
- `templates/outbound-callee-sim.jsonl`
- `templates/inbound-caller-sim.jsonl`

```jsonl
{"kind":"Caller","spec":{"mode":"inbound_sip"}}
{"kind":"Telephony","spec":{"dial_in":"+15551234567"}}
```

```yaml
# config.yaml — defaults only (optional)
telephony:
  outbound_trunk_id: "ST_xxxxxxxxxxxx"
  dial_in: "+15551234567"
  sim_inbound_number: "+15559876543"  # Gemini answers here for outbound_sim_callee
  handset_isolation: mute_and_unsubscribe  # outbound_human_pickup after human answers
  prepare_ms: 3000
  wait_until_answered: true
```

SIP asserts:

```json
{"kind":"Assert","spec":{"sip":{"participant_present":true,"dial_answered":true,"call_status_any":["active"]}}}
```

Precedence: **scenario `Telephony.*` > config `telephony.*` > built-ins**.  
Full guide: https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/telephony.md


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
| `cues` | `list_cues` |
| `validate` | `validate_scenario` |
| `export` | `export_scenario` |
| `scenario-init` | `init_scenario` |
| `execute` | `execute_scenario` (flags: ``--name``, ``--repeat N --pass-at-k K``, ``--strict-judge``) |
| `execute-all` | `execute_scenarios` (suite matrix + CI gate; flags: ``--repeat --pass-at-k --parallel N``, ``--strict-judge``) |
| `execute-dict` | `execute_scenario_dict` (flag: ``--name`` / MCP ``run_name``) |
| `scenario-from-run` | `scenario_from_run` |
| `status` | `get_run_status` |
| `log` | `get_run_log` |
| `report` | `get_run_report` |
| `compare` | `compare_runs` (optional `--baseline` hard regression gate) |
| `runs` | `list_runs` |
| `mcp` | *(stdio server — all tools above)* |

There is **no** separate `run` command — always validate-then-run via `execute*`.

Golden baseline gate (CI): treat run A as baseline, fail exit `1` if candidate regresses:

```bash
lks compare <baseline-run> <candidate-run> --baseline --root /path/to/target
# thresholds: --max-ttfw-regression-ms · --max-turn-p95-regression-ms ·
#             --max-duration-regression-ms · --max-barge-recovery-drop
```

Without `--baseline`, `compare` is a soft metric/assert diff only.

MCP for coding agents (Claude, Cursor, Windsurf, VS Code, …):

```json
{
  "mcpServers": {
    "livekit-agent-simulator": {
      "command": "lks",
      "args": ["mcp"]
    }
  }
}
```

---

## 4. Reports & web player

Directory: `.agent-sim/reports/<run-id>/`

`run_id` = `{NNN}-{slug}-{YYYYMMDD}-{HHMMSS}-{xxxx}` where:

- **`NNN`** — auto sequence (`001`, `002`, …) from existing folders under `reports/` (parallel-safe)
- **`slug`** — scenario id by default, or ``--name`` / MCP ``run_name`` override
- **timestamp + hex** — UTC stamp + 4 hex chars so ids stay unique even if a report folder was deleted but SQLite still has the old row

Examples: `001-smoke-hello-20260716-144623-a1b2`, `002-demo-20260716-144630-c3d4`. Suite matrices still write `suite-YYYYMMDD-HHMMSS.{json,md}` beside run folders (not a run_id).

| File | Contents |
|------|----------|
| `events.jsonl` | Canonical event stream |
| `timeline.md` | Human narrative table |
| `summary.json` | Duration, turns, **`metrics`** (TTFW / turn p50-p99 / recovery / barge rate / talk_ratio), judge, **`caller.behavior_summary`**, `script_verify`, `assert_verify` |
| `meta.json` | `run_id`, `scenario_id`, optional `run_name`, room, config snapshot (no secrets) |
| `conversation.wav` | Stereo PCM if `observe.record_audio: true` |
| `cues.json` | Built on demand by `web` for transcript↔audio sync + markers |

Observation layers are L0 room events, L1 transcripts, L2 custom data topics, and
L3 standard LiveKit Agents session events. L3 records `tool.*` and `session.*`
automatically for SDK agents; custom `tool_event_patterns` remain a fallback.

```bash
lks report 001-smoke-hello-20260716-144623-a1b2 --root /path/to/target   # full summary (includes caller.behavior_summary)
lks log 001-smoke-hello-20260716-144623-a1b2 --kind "transcript.*" --root /path/to/target
lks log 001-smoke-hello-20260716-144623-a1b2 --kind "sim.script*" --root /path/to/target
# --kind: one kind or one prefix (trailing *); not a comma-separated list
lks runs --root /path/to/target
lks web --root /path/to/target              # home list of all scenarios/runs (auto-updates ~3s)
lks web 001-smoke-hello-20260716-144623-a1b2 --root /path/to/target     # deep-link a specific run
# Opens http://127.0.0.1:8765 — stereo L=sim R=agent; timeline bands + chips for
# barge / backchannel / false_interrupt / dtmf / silence / recovery / tools
# Middle column shows agent actions (script cues + tool cards with args/output when L3 enabled)
# Home list + player sidebar poll GET /api/runs; open player cues stay one-shot until you re-open a run
```

No Node/Vite on the user machine. Player assets ship inside the wheel (built in CI from `web/dist/`).

---

## 5. Common failures

| Symptom | What to check |
|---------|----------------|
| `preflight` config fail | Missing `.agent-sim/config.yaml` → `init` first |
| `livekit.api` fail | `url` / `api_key` / `api_secret` |
| `dispatch.agent_timeout` | Worker process up? `agent_name` exact match (e.g. `…-local` vs prod)? |
| Agent joins but silent | `Execute.first_speaker`; worker may need `Dispatch.metadata` |
| Sim never speaks after agent | Normal nudge only when `first_speaker=agent` and no Script |
| Judge skipped | Need `PassCriteria` **and** `judge:` block; HTTP needs `base_url`+`api_key` (or `JUDGE_*` env) |
| Judge HTTP error | `base_url` reachable? OpenAI `/chat/completions` + `stream:false`; model id correct on gateway? |

| No `conversation.wav` | Set `observe.record_audio: true` |
| Scenario JSON error | Remove `#` comments; only full-line `//` allowed |
| scenario-from-run draft says "DRAFT — review" | The draft is a starting point; tighten Persona/Assert before promoting to CI |
| pass@k flake | Increase ``--repeat`` or relax ``--pass-at-k``; single-shot runs are not statistically conclusive |
| SIP parse/preflight fail | Set `telephony.outbound_trunk_id` + `call_to`/`dial_in`; mode only in `Caller` |
| outbound rings wrong number | `call_to` must be sim/Gemini DID, not a personal handset by default |
| inbound no agent room | Set `Telephony.agent_room` / `agent_room_name_template` or fix inbound dispatch rule |

---

## 6. Rules for coding agents

- **Generic core:** do not hardcode consumer languages, agent IDs, data topics, or business rules in package `src/`.
- **Customize in target** `.agent-sim/` only (config, scenarios, plugins).
- **No legacy shims** in this package while pre-1.0 — one clear flag name.
- Prefer `execute` / `execute_scenario` / `execute_scenario_dict` over custom Python runners.
- Deep package rules + research loop: https://github.com/quangdang46/livekit-agent-simulator/blob/main/AGENTS.md
- Consumer wiring examples only: https://github.com/quangdang46/livekit-agent-simulator/blob/main/docs/portability.md (load when setting up a target, not for core bugs).

## People-pleaser counters (sim caller)

LLM sim callers over-cooperate. For CI, **do not rely on traits alone**:

- Script/Behavior fixed refuse lines + Assert `constraint_respected`
- Script `hang_up` + Assert `ended_by` when testing hangup threats
- Examples: `templates/examples/people-pleaser-refuse-card.jsonl`, `people-pleaser-hangup-threat.jsonl`

## Suggested suite mix (Cekura-style)

For a regression suite under `.agent-sim/scenarios/`:

| Share | Profile | Examples |
|---|---|---|
| ~60% | Standard / polite / clean | `smoke-hello`, happy-path goals |
| ~20% | Interrupt + noise | barge recovery, `interruption_rate`, ambient noise |
| ~10% | Non-native / confused / slow | traits `non_native`, `confused`, `amd-slow-pickup` |
| ~10% | Edge | people-pleaser refuse/hangup, silent caller, draft telephony |

Tag scenarios (`smoke`, `regression`, `draft`, `policy`) and gate CI on a small **blocking** subset first.
