# Plan Report — SimLeg telephony (portable)

**ID:** PLAN-20260713-telephony-simleg  
**Date:** 2026-07-13 (portable rewrite)  
**Repo:** livekit-agent-simulator (lk-sim)  
**Status:** Waiting for OK — reply **go ahead** to implement **T0 + T1** first

---

## 0. Summary (read this first)

| | |
|---|---|
| **Goal** | RTC + inbound SIP + outbound SIP; **Gemini always replaces the human**; same forensic log/assert/judge; one design pattern for all modes. |
| **Constraint** | Package is **target-agnostic**. No consumer product names, agent IDs, dashboard keys, or business paths in `src/`. All project wiring lives in that target’s `.agent-sim/`. |
| **Recommend** | **Template Method + Strategy + Factory**. Primary SIP = **LiveKit Cloud hairpin** (Gemini WebRTC in sim-room; LiveKit bridges SIP). Optional in-process RTP vendor = **T6 only**. |
| **Risk** | T0/T1 **Low**. T2/T3 **Med** (cross-room observe + DID/dispatch config). T6 High (deferred). |
| **Not in T2** | In-process RTP callee as primary; licensed-or-not Go ports; personal mobile as default `call_to`. |

**Portability rule (locked):** lk-sim dials **any** LiveKit voice agent. Core exposes knobs (`Caller`, `Telephony`, opaque `Dispatch.metadata`, observe config). The target fills values. If a feature only works for one monorepo, it does not belong in `src/`.

---

## 1. Product goals (locked)

1. **Gemini = simulated human** on every topology — no observe-only PSTN mode.
2. **Three modes, one purpose:** exercise the agent as a real conversation + forensic artifacts.
3. **One SimBrain** — `GeminiCallerBridge` + Persona/Script/Behavior — not cloned per mode.
4. **One ObserveReport** — Observer, EventWriter, WAV, assert, judge, suite, `pass@k`.
5. **Mode only changes the transport leg** (`SimLeg`).
6. **Provider-agnostic** — core knows LiveKit `sip_trunk_id` / DID / room only; no carrier branches in `src/`.
7. **Portable core** — no hardcoding of consumer dispatch keys, agent names, languages, timezones, or tool topics in package defaults.
8. **Dynamic configuration** — scenario overrides config; config overrides built-ins; missing SIP fields fail fast with actionable errors.

### Mode × Gemini role

| `Caller.mode` | Gemini role | Agent path | Rooms |
|---|---|---|---|
| `webrtc_sim` (default) | Caller | WebRTC same room | 1 |
| `inbound_sip` | **Caller** (dials in) | Agent answers inbound | 2 (hairpin) |
| `outbound_sip` | **Callee** (answers) | Agent (or harness) dials out | 2 (hairpin) |

---

## 2. Evidence (generic — not a product dependency)

Research informed the design. **Nothing below is imported or required by core.**

### 2.1 LiveKit platform (docs + SDK)

| Source | Finding used |
|---|---|
| [Outbound calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/) | `CreateSIPParticipant`; `wait_until_answered`; inline trunk option |
| [SIP participant](https://docs.livekit.io/reference/telephony/sip-participant/) | Attributes; errors when `wait_until_answered` |
| [Dispatch rule](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/) | Direct / Individual / Callee → room naming for inbound |
| [Testing](https://docs.livekit.io/telephony/testing/) | Test call → inspect room + SIP participant + logs |
| [SIP primer](https://docs.livekit.io/reference/telephony/sip-primer/) | PSTN ↔ provider ↔ LiveKit SIP |
| livekit/sip `callStatus` | `dialing` → `ringing` → `automation` → `active` → `hangup` |
| livekit-api **1.1.1** (locked in this repo) | `create_sip_participant(create, *, timeout, trunk_id, outbound_trunk_config)` |
| Community “404 No trunk” | Outbound trunk address = **carrier termination URI**, not LiveKit host; E.164 matters |

### 2.2 This package today

| Item | Value |
|---|---|
| LiveKit config | `url` + `api_key` + `api_secret` + `agent_name` (+ opaque `dispatch_metadata`) |
| Phase 3 | Hard-coded `connect_simulator` + fixed sim identity |
| Python | `>=3.10,<3.14` — stdlib `audioop` removed in 3.13 (relevant only if T6 vendors raw RTP) |
| Portability | AGENTS.md + `docs/portability.md` — black-box target, opaque dispatch |

### 2.3 Reference implementations (gitignored clones / public examples)

| Source | Role in plan |
|---|---|
| sip-to-ai (Apache-2.0) | **T6 optional** SIP/RTP vendor candidate — AI-decoupled core |
| sip-proxy (no LICENSE) | **Pattern only** — zero file copy |
| outbound-caller-python | Dial sequence: prepare brain before dial; `wait_until_answered` |
| livekit/sip lktest-sip-outbound | Two-room trunk loopback validates hairpin idea |
| Typical consumer env patterns | Confirmed Cloud + stored trunk IDs are common; **not** encoded in package |

### 2.4 Concepts clarified (any target)

```text
Trunk ID (ST_…)     = route used to place/accept SIP — NOT a phone number
sip_call_to / phone = DESTINATION that rings (who answers)
outbound manual     = dial a human’s handset
outbound sim        = dial a sim/DID that Gemini answers
inbound sim         = Gemini dials the agent’s inbound DID
```

---

## 3. Architecture

### 3.1 Design patterns (mode switch)

```text
RunOrchestrator  — Template Method (7 phases, single path)
  1 RoomLifecycle
  2 ObservePrepare
  3 SimLeg.connect()   ◄── Strategy (ONLY mode fork)
        Factory(effective_mode)
        ├─ WebRtcSimLeg
        ├─ InboundSipSimLeg
        └─ OutboundSipSimLeg
  4 SimBrain           GeminiCallerBridge + Script   (shared)
  5 Converse           _conversation_loop            (shared)
  6 ObserveReport      Observer + recorder           (shared)
  7 VerifyJudge        asserts + judge               (shared)
```

| Pattern | Role |
|---|---|
| **Template Method** | One pipeline — no three orchestrators |
| **Strategy** | `SimLeg` protocol — three connect implementations |
| **Factory** | `sim_leg_factory(mode)` from `Caller.mode ?? "webrtc_sim"` |
| **Adapter** | `LiveKitAdapter.create_sip_participant` wraps livekit-api |

Not used: mid-run mode state machine; Abstract Factory; per-mode brain forks.

### 3.2 Why Cloud hairpin is primary (portable reason)

```text
❌ Not primary:
   INVITE → localhost SIP stack → RTP ↔ Gemini
   LiveKit Cloud cannot reach a laptop listener without public exposure.

✅ Primary:
   sim-room:    Gemini WebRTC  +  one SIP call leg
   agent-room:  agent under test + other SIP call leg
   LiveKit terminates G.711/RTP and bridges audio.
   Gemini never handles RTP. Same GeminiCallerBridge as WebRTC.
```

Works for any Cloud project with trunks/DIDs. Self-host SIP + in-process callee is **T6**, not a requirement.

### 3.3 SimLegHandle (normalized, mode-agnostic)

```text
SimLegHandle
  agent_room: rtc.Room        # Observer ALWAYS attaches here
  sim_room: rtc.Room          # WebRTC: same object as agent_room; SIP: Gemini room
  sim_identity: str           # WebRTC: package sim identity; SIP: SIP participant id in agent-room
  agent_identity: str
  disconnect() / cleanup()    # tear down all rooms this leg created
```

### 3.4 Cross-room observation (generic rules)

| Concern | Rule |
|---|---|
| Transcripts / tools / agent data | Observer on **agent-room** |
| Gemini mic publish | **sim-room** WebRTC |
| Gemini listen path | SIP participant track in **sim-room** (bridged agent audio) |
| “Human” role for asserts | `sim_identity` = SIP participant identity **in agent-room** |
| WAV L=sim R=agent | **Default:** L = Gemini local PCM (sim-room); R = agent audio as heard on agent-room (fallback: SIP track on sim-room). Fixed rule — not left open. |

### 3.5 Runtime sequences

#### `webrtc_sim` (unchanged)

One room: Gemini WebRTC + agent. No SIP.

#### `outbound_sip` — Gemini answers

```text
create sim-room → connect Gemini WebRTC (callee ready)
→ create agent-room → dispatch agent → wait agent joined
→ prepare_ms
→ create_sip_participant(
     room = agent-room,
     trunk = effective_outbound_trunk,
     call_to = effective_call_to,          # sim/DID Gemini answers
     wait_until_answered = true
   )
→ wait sip.callStatus active
→ Observer(agent-room) + Gemini brain(sim-room)
→ converse → cleanup both rooms
```

#### `inbound_sip` — Gemini dials

```text
create sim-room → connect Gemini WebRTC (caller ready)
→ create_sip_participant(
     room = sim-room,
     trunk = effective_outbound_trunk,
     call_to = effective_dial_in,          # agent-side inbound DID
     wait_until_answered = true
   )
→ agent joins agent-room via that project’s inbound dispatch rule
→ Observer(agent-room) + Gemini brain(sim-room)
→ converse → cleanup
```

#### `agent_dials` (T5 optional)

Harness dispatches only; the **agent process** places `CreateSIPParticipant` (cooperative agents). Core still does not parse job metadata keys — only waits for SIP participant + `active` on agent-room.

---

## 4. Dynamic config contract

### 4.1 Precedence (always)

```text
Scenario field  >  config.yaml  >  built-in default  >  fail-fast if required
```

| Field | Scenario | Config | Built-in |
|---|---|---|---|
| mode | `Caller.mode` | **none** (mode is never in config) | `webrtc_sim` |
| call_to | `Telephony.call_to` | `telephony.sim_inbound_number` | required if `outbound_sip` |
| dial_in | `Telephony.dial_in` | `telephony.dial_in` | required if `inbound_sip` |
| trunk | `Telephony.sip_trunk_id` | `telephony.outbound_trunk_id` | required if SIP mode |
| prepare_ms | `Telephony.prepare_ms` | `telephony.prepare_ms` | `3000` |
| wait_until_answered | `Telephony.wait_until_answered` | `telephony.wait_until_answered` | `true` |

### 4.2 `config.yaml` shape (portable placeholders)

```yaml
# .agent-sim/config.yaml — lives in TARGET repo (gitignored)
project: your-project

livekit:
  url: "wss://your-project.livekit.cloud"
  api_key: "APIxxxxxxxx"
  api_secret: "secretxxxxxxxx"
  agent_name: "your-agent-name"
  # Opaque JSON — core never interprets keys
  # dispatch_metadata: '{"yourProjectKey":"value"}'
  agent_join_timeout_ms: 25000

simulator:
  google_api_key: "AIzaxxxxxxxx"
  language: "en-US"
  voice:
    mode: realtime
    provider: google
    model: "gemini-3.1-flash-live-preview"
    voice: "Puck"
    language: "en-US"

judge:
  provider: google
  model: "gemini-2.5-flash"
  temperature: 0

observe:
  timezone: "UTC"
  lk_transcription: true
  record_audio: true
  data_topics: []
  transcript_payload_types:
    - "transcript_turn"
  silence_threshold_ms: 10000
  turn_taking_warn_ms: 2500

# Optional — omit entirely for WebRTC-only targets
telephony:
  outbound_trunk_id: "ST_xxxxxxxxxxxx"
  inbound_trunk_id: "ST_yyyyyyyyyyyy"    # optional metadata / preflight
  dial_in: "+15551234567"                # default agent inbound DID
  sim_inbound_number: "+15559876543"     # default Gemini callee DID
  prepare_ms: 3000
  wait_until_answered: true
  krisp_enabled: false
  # Optional: how to resolve agent-room for inbound hairpin
  # inbound_room_strategy: dispatch_rule   # or: explicit
  # agent_room_name_template: "sip-in-{run_id}"
```

**WebRTC-only targets:** no `telephony:` block required.

### 4.3 Scenario shapes

**WebRTC (default — no Caller line needed):**

```jsonl
{"kind":"Scenario","spec":{"id":"smoke-hello","tags":["webrtc"]}}
{"kind":"Persona","spec":{"name":"Alex","brief":"…"}}
{"kind":"Execute","spec":{"first_speaker":"agent","max_turns":5}}
```

**Outbound (Gemini callee):**

```jsonl
{"kind":"Caller","spec":{"mode":"outbound_sip"}}
{"kind":"Telephony","spec":{"call_to":"+15559876543"}}
{"kind":"Persona","spec":{"brief":"You answered a call. Be brief."}}
{"kind":"Execute","spec":{"first_speaker":"user","timeout_s":120}}
{"kind":"Dispatch","spec":{"metadata":"{\"yourProjectKey\":\"value\"}"}}
```

**Inbound (Gemini caller):**

```jsonl
{"kind":"Caller","spec":{"mode":"inbound_sip"}}
{"kind":"Telephony","spec":{"dial_in":"+15551234567"}}
{"kind":"Persona","spec":{"brief":"You call about a billing question."}}
{"kind":"Execute","spec":{"first_speaker":"user","timeout_s":120}}
```

`Dispatch.metadata` remains an **opaque JSON string**. Core validates JSON syntax only.

### 4.4 Target-owned provisioning (outside package)

Each target that wants SIP scenarios must provision (once) in LiveKit + its own `.agent-sim/`:

1. Outbound trunk id (carrier termination address correctly set).
2. Agent inbound DID + inbound trunk + dispatch rule (room naming known).
3. Sim/Gemini answer DID (or SIP URI) for outbound scenarios — **not** a developer’s personal phone as the package default.
4. Secrets only in target config (gitignored).

Package docs describe the checklist with placeholders. Package code never embeds a specific project’s trunk or number.

---

## 5. Port / copy matrix

### T0–T4 primary path — no vendor SIP stack

| Source | Copy into `src/`? | Take |
|---|---|---|
| sip-to-ai | No | — |
| sip-proxy | No (no license) | Optional later ideas only |
| outbound-caller-python | No | Sequence ideas only |
| livekit-api | Already a dependency | `create_sip_participant` |

### T6 optional (offline / self-host SIP)

Apache-2.0 sip-to-ai SIP/RTP core only; strip multi-AI clients; fix `audioop` → numpy codec for 3.13; `THIRD_PARTY_NOTICES.md`. Not on the critical path.

---

## 6. Implementation steps

### T0 — Extract `WebRtcSimLeg` (refactor only)

- [ ] `livekit/sim_leg.py` — Protocol, `SimLegHandle`, `WebRtcSimLeg`
- [ ] Orchestrator Phase 3 uses leg; `sim_room is agent_room`
- [ ] `pytest -q` green; existing smoke scenarios unchanged

**Accept:** Zero behavior change.

### T1 — Contract + factory + docs

- [ ] `Caller` / `Telephony` kinds + merge helpers (`scenario` overrides `config`)
- [ ] `sim_leg_factory` — SIP modes raise clear `NotImplementedError` until T2/T3
- [ ] `TelephonyConfig` + redaction of trunk/numbers in snapshots
- [ ] `docs/telephony.md` — topologies, precedence, portable placeholders, preflight
- [ ] Unit tests: parse, merge, factory, backward compat (no `Caller` line)

**Accept:** Missing required SIP fields fail at parse/preflight; WebRTC identical.

### T2 — `outbound_sip` (Gemini callee)

- [ ] `LiveKitAdapter.create_sip_participant` + structured dial errors
- [ ] `OutboundSipSimLeg` per §3.5
- [ ] Events: `outbound.dial_*`, `sip.participant_connected`, `sip.call_status`
- [ ] Observer agent-room; brain sim-room; SIP track for Gemini ears
- [ ] WAV default rule §3.4
- [ ] Neutral template `templates/outbound-callee-sim.jsonl`
- [ ] Manual validation against **any** configured target trunk (not package CI)

**Accept:** SIP participant reaches `active`; bidirectional audio; full forensic artifacts; no consumer-specific asserts required.

### T3 — `inbound_sip` (Gemini caller)

- [ ] `InboundSipSimLeg`; reuse SIP + cross-room from T2
- [ ] Neutral template `templates/inbound-caller-sim.jsonl`
- [ ] Document `inbound_room_strategy` for resolving agent-room

**Accept:** Same artifact set as WebRTC + SIP events.

### T4 — SIP asserts + suite columns

- [ ] Assert kinds: `sip_call_status`, `sip_participant_present`
- [ ] summary/suite: `mode`, `dial_ms`, `sip_status`
- [ ] Fixture unit tests (no trunk)

### T5 — `agent_dials` (optional)

- [ ] Dispatch + wait for SIP on agent-room
- [ ] Opaque metadata only — never parse consumer keys

### T6 — Optional RTP vendor (deferred)

- [ ] Only if offline/self-host needed

---

## 7. Files to touch

| File | Change |
|---|---|
| `src/.../livekit/sim_leg.py` | **New** — protocol + legs |
| `src/.../livekit/adapter.py` | `create_sip_participant` |
| `src/.../run_orchestrator.py` | Phase 3 factory + multi-room cleanup |
| `src/.../scenario.py` | Caller/Telephony + merge |
| `src/.../scenario_from_dict.py` | Same |
| `src/.../config.py` | `TelephonyConfig` + redaction |
| `src/.../livekit/observer.py` | agent-room + SIP `sim_identity` |
| `src/.../gemini/live_session.py` | Agent audio from WebRTC agent **or** SIP track |
| `src/.../preflight.py` | SIP scenario checks when mode is SIP |
| `src/.../asserts.py` | SIP kinds (T4) |
| `docs/telephony.md` | **New** (portable) |
| `templates/outbound-callee-sim.jsonl` | **New** (neutral placeholders) |
| `templates/inbound-caller-sim.jsonl` | **New** |
| `tests/test_sim_leg*.py`, `tests/test_scenario_caller_telephony.py` | **New** |
| `THIRD_PARTY_NOTICES.md` | Only if T6 |
| `WIP.md` | When implementation starts |

**Not in primary path:** any consumer app path; `sip_callee/**`.

---

## 8. Events & asserts (core)

```text
outbound.dial_started | outbound.dial_answered | outbound.dial_failed
inbound.dial_started  | inbound.answered       | inbound.dial_failed
sip.participant_connected
sip.call_status   { status: dialing|ringing|automation|active|hangup }
```

Shared unchanged: `transcript.*`, `tool.*`, `turn.*`, script verify, PassCriteria, suite gate.

T4 asserts: `sip_participant_present`, `sip_call_status` (e.g. expect `active`).

Consumer-specific behaviors (timers, tools, dashboard IDs) belong in **that target’s** scenarios/plugins — never in package DoD.

---

## 9. CI / test strategy

| Tier | When | What | Trunk? |
|---|---|---|---|
| PR | Now | Unit: parse, factory, config, mocked SIP API, assert fixtures | No |
| Local manual | Dev | Target’s `.agent-sim/config.yaml` + real trunks if available | Target-owned |
| CI telephony E2E | Defer | Optional nightly — not a package gate | Later |

`@pytest.mark.telephony` skipped unless `--run-telephony` or fixture supplies trunk.

---

## 10. Risk matrix

| Risk | Level | Mitigation |
|---|---|---|
| Cross-room observe wrong room | Med | Observer always agent-room; unit-test binding |
| Unpredictable inbound room name | Med | `inbound_room_strategy` + template; preflight docs |
| PSTN cost / wrong number | Med | Target allowlists; docs; no personal defaults in package |
| WebRTC regression | Low | T0 zero change; default `webrtc_sim` |
| SDK 1.1.1 vs newer docs | Low | Code against installed 1.1.1; mock service |
| Parsing opaque dispatch keys | **High if done** | Forbidden in core |
| Fit-to-one-repo drift | Med | AGENTS.md review; neutral templates; no product names in `src/` |
| In-process RTP as T2 gate | Avoided | T6 only |

---

## 11. Anti-patterns

- `mode` in shared `config.yaml`
- Observe-only dial without Gemini
- Duplicate converse/observe per mode
- Hardcode consumer keys, agent names, languages, trunks, or DIDs in `src/`
- Package templates that embed a real product id
- Port third-party SIP stacks without license review
- Assume Cloud can INVITE localhost
- Make personal mobile the documented default `call_to`
- Require a specific consumer behavior (timers, tools) for core SIP acceptance

---

## 12. Resolved decisions

| # | Decision |
|---|---|
| 1 | Primary SIP path = Cloud hairpin, not in-process RTP |
| 2 | CI trunk deferred; local/target manual only |
| 3 | `wait_until_answered` default **true** |
| 4 | v1 uses stored `sip_trunk_id` (inline trunk later if needed) |
| 5 | Mode switch = Template Method + Strategy + Factory |
| 6 | Scenario > config > built-in |
| 7 | Package independent of any single agent repo |
| 8 | WAV mix default fixed (§3.4) |
| 9 | Consumer validation suites live only under that target’s `.agent-sim/` |

### Target owner ops (not package work)

1. Configure LiveKit trunks/DIDs for that project.  
2. Know inbound dispatch room naming.  
3. Put values in **that** repo’s `.agent-sim/config.yaml`.  

---

## 13. Dependency graph

```text
T0 WebRtcSimLeg
 └─► T1 Caller/Telephony/factory/docs
      ├─► T2 outbound_sip ──► T4 asserts
      │         └─► T5 agent_dials (optional)
      └─► T3 inbound_sip  ──► T4
T6 RTP vendor independent / deferred
```

---

## 14. Definition of done (package)

- [ ] `webrtc_sim` bit-identical to pre-change  
- [ ] `outbound_sip`: Gemini answers; SIP `active`; audio both ways; forensic complete  
- [ ] `inbound_sip`: Gemini dials; agent joins; forensic complete  
- [ ] Missing trunk/number fails with actionable parse/preflight error  
- [ ] No secrets, product names, or consumer keys in package source/templates  
- [ ] Unit tests PR-green without trunk  
- [ ] `docs/telephony.md` uses only portable placeholders  

---

## 15. Next step

Reply **go ahead** to implement **T0 + T1** (zero SIP infra).  
T2+ uses whatever trunks the **target** configures — package stays independent.

---

## Appendix A — Implementer sequences

### Outbound Pattern A (harness dials into agent-room)

```text
sim-room + Gemini → agent-room + dispatch → prepare_ms
→ create_sip_participant(agent-room, call_to=sim DID, wait_until_answered=True)
→ Observer(agent-room) + brain(sim-room) → converse → cleanup
```

### Inbound

```text
sim-room + Gemini → create_sip_participant(sim-room, call_to=agent dial_in)
→ wait agent on agent-room → Observer(agent-room) + brain(sim-room) → converse → cleanup
```

## Appendix B — Research links

- https://docs.livekit.io/telephony/making-calls/outbound-calls/  
- https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/  
- https://docs.livekit.io/telephony/testing/  
- https://docs.livekit.io/reference/telephony/sip-participant/  
- https://docs.livekit.io/reference/telephony/sip-primer/  
- https://github.com/livekit/sip  
- https://github.com/aicc2025/sip-to-ai (T6 candidate)  
- https://github.com/livekit-examples/outbound-caller-python  
- This repo: `AGENTS.md`, `docs/portability.md`
