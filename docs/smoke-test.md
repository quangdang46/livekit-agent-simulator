# Smoke test — first end-to-end run

Goal: prove the whole chain works against a local agent:
room create → agent dispatch → agent joins → sim caller joins → Gemini talks →
transcripts + events logged → report written.

## Prerequisites

1. Your LiveKit **agent process** is running and registered with the `agent_name` in
   `.agent-sim/config.yaml`.
2. LiveKit Cloud (or self-hosted) URL + API key/secret.
3. A Google API key with access to `gemini-3.1-flash-live-preview` (same key works
   for the `gemini-2.5-flash` judge).

## Steps

All commands run from the repo you want to test (`--root` defaults to CWD):

```powershell
# 1. Scaffold .agent-sim/ (gitignored automatically)
uv run --directory /path/to/livekit-agent-simulator lk-sim init

# 2. Fill in credentials
#    .agent-sim/config.yaml → livekit.url / api_key / api_secret / agent_name
#                             simulator.google_api_key

# 3. Verify connectivity BEFORE burning a run
uv run --directory /path/to/livekit-agent-simulator lk-sim preflight

# 4. Run the bundled smoke scenario (2 turns, 90s cap)
uv run --directory /path/to/livekit-agent-simulator lk-sim execute smoke-hello

# 5. Inspect
uv run --directory /path/to/livekit-agent-simulator lk-sim report <run-id>
uv run --directory /path/to/livekit-agent-simulator lk-sim log <run-id> --kind "transcript.*"
uv run --directory /path/to/livekit-agent-simulator lk-sim log <run-id> --kind "tool.*"
```

## What success looks like

- `dispatch.agent_joined` appears in the log within `agent_join_timeout_ms`.
- `sim.gemini_connected`, `sim.mic_published`, `sim.agent_audio_bridged` events exist.
- `transcript.agent.final` (from `lk.transcription`) and `transcript.user.final`
  (from the sim's own Gemini transcription) alternate per turn.
- `reports/<run-id>/timeline.md` reads like a call narrative; `summary.json` has
  turn-taking percentiles and (if PassCriteria set) a judge verdict.
- With `observe.record_audio: true` (default in template): `reports/<run-id>/conversation.wav`
  is a local stereo file (L=sim caller, R=agent). No LiveKit Egress. Override language/timezone
  in `.agent-sim/config.yaml` for your market (package defaults are `en-US` / `UTC`).
- For a scenario that calls an SDK tool: `tool.start` + `tool.end`/`tool.error` and
  `session.chat_history` appear; `meta.json` does not list `tool_events` in `observe_gaps`.

## Common failures

| Symptom | Meaning |
|---|---|
| `Preflight failed: livekit.api ... 401` | Wrong api_key/api_secret or URL |
| `Agent ... did not join room` | Agent not running, or `agent_name` mismatch |
| `sim.error where=gemini->lk ... 1011` | Wrong Live model name or key lacks Live API access |
| `dead_call_silence` end reason | Agent joined but never spoke — check agent logs |
| No `tool.*` events | Confirm the agent uses a compatible LiveKit Agents SDK and `observe.lk_agent_session: true`; for custom agents, configure `observe.tool_event_patterns` (see `docs/portability.md`) |

For consumer-specific dispatch keys and data topics, see [portability.md](portability.md).
