# WIP — gaps toward replacing a developer talking to the agent

Goal: **`lk-sim` replaces manual “open mic and chat with the voice agent”** for day-to-day QA — full-stack LiveKit room, black-box agent, forensic report + web replay.

Not the goal (different products): LiveKit **in-process** text unit tests inside agent code; 50k concurrent load platforms; full production observability SaaS.

---

## Research note (2026-07-12)

Sources: LiveKit Agents testing docs, Hamming metrics / persona templates, Coval / Cekura / Okareo / Future AGI / Phonely landscape; internal audit of `asserts.py`, `event_writer.py`, `caller-pattern-plan.md`.

### How the industry splits testing

| Layer | Who owns it | What it is |
|---|---|---|
| **In-process unit / session** | LiveKit Agents `AgentSession` + pytest/Vitest | Fake STT/LLM/TTS, `result.expect…`, LLM judge, metrics (EOU, TTFT, TTFB) — **no real WebRTC room** |
| **Black-box room / audio E2E** | Hamming, Coval, Cekura, Bluejay, Roark, **lk-sim** | Real (or near-real) room + audio pipeline + sim caller |
| **Load** | `lk perf agent-load-test`, Hamming concurrent | Many WebRTC rooms; SIP load is provider-specific |
| **Prod observe** | OTel, Hamming/Coval/Cekura online eval | Drift, P90 latency, alerts |

LiveKit official stance: native tests for **agent logic**; for **full audio pipeline** they point to third-party tools. lk-sim sits in that E2E slot — **complement**, not replace, Agents pytest.

### Competitive snapshot (2026-07-12)

| Capability | lk-sim | Hamming | Coval | Cekura | Future AGI | LiveKit pytest |
|---|---|---|---|---|---|---|
| Real LiveKit room | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (text session) |
| AI persona caller | ✅ Gemini Live | ✅ | ✅ | ✅ | ✅ | ❌ |
| Barge / silence / noise | ✅ Script + mixer | ✅ | ✅ | ✅ | partial | ❌ |
| Recovery assert (+ timing) | ✅ `Assert.outcomes.recovery` | ✅ | ✅ | ✅ | roadmap | ❌ |
| Forensic local log + web | ✅ strong | cloud | cloud | cloud | SDK | partial |
| MCP / coding-agent | ✅ **diff** | ❌ | partial | ❌ | ❌ | ❌ |
| CLI portable / self-host | ✅ MIT | closed | closed | closed | SDK+cloud | open framework |
| Latency metrics hard | ✅ Assert `latency` + `summary.metrics` | ✅ | ✅ | ✅ | roadmap | metrics helpers |
| pass@k | ✅ ``--repeat --pass-at-k`` | ✅ | ✅ | ✅ | ✅ | flaky patterns |
| Fail → golden | ✅ ``scenario-from-run`` | ✅ | ✅ | partial | partial | manual |
| Accent matrix | ❌ | ✅ | ✅ | partial | partial | ❌ |
| SIP / telephony (in+out, Gemini sim) | ⚠️ SimLeg design done; T2/T3 TBD | ✅ | ✅ | ✅ | ✅ | ❌ |
| Load test | ❌ | ✅ | partial | ✅ | cloud | ❌ |
| Prod observe | ❌ | ✅ | ✅ | ✅ | eval | OTel elsewhere |

**Niche:** open-source, local-first, forensic-first, MCP-native black-box LiveKit — dev/coding-agent loop, not Hamming/Coval SaaS.

### Metrics industry cares about

| Metric | Industry target (Hamming-ish) | lk-sim today |
|---|---|---|
| Turn-taking / TTFW | P90 e2e &lt; ~3.5s; first word often &lt;2.5s | ✅ `summary.metrics` (p50/p95/p99, TTFW, recovery, barge rate) + Assert type **`latency`** + suite columns |
| Barge-in recovery | Agent re-engages; recovery &gt;90% | ✅ Assert `recovery` + optional `max_ms_after_barge_to_agent_final` + `caller.behavior_summary` |
| Task completion | &gt;90% | Assert + PassCriteria judge |
| Flake / pass@k | Statistical eval | ✅ ``--repeat N --pass-at-k K`` |
| Fail → golden | Prod/sim failure → permanent test | ✅ ``scenario-from-run <run-id> [--write]`` |
| Suite CI gate | Non-zero exit, matrix | ✅ `execute-all` matrix + hard status/assert/script (judge soft unless `--strict-judge`) |

### Audio realism competitors sell

Accent / noise / mid-sentence barge-in **timing** / multi-voice — we have traits, mixer, cues catalog, vocal barge WAVs (en/vi), recovery timing assert. Still weak on **accent matrix**, SNR levels as first-class, DTMF/IVR scripting, multi-voice packs, WER/audio-native eval.

---

## Already in good shape

| Capability | Notes |
|---|---|
| Black-box room + dispatch | Opaque metadata; any LiveKit agent |
| Gemini Live sim caller | Persona / Context / Execute |
| Caller character (Hamming-aligned) | `constraints`, `speech_conditions`, `Behavior` → Script compile |
| Traits library | impatient, interrupts, elderly, angry, backchannel, hangup_threat, code_switch, … |
| Scripted cues | `agent_speaking`, `gemini_text` / `room_pcm` |
| Cue catalog multi-repo | `builtin:…`, `.agent-sim/cues/`, `cues.aliases` / `dirs` |
| Parallel speech+noise | `ParallelMicMixer` |
| Barge-in (harder) | vocal PCM + blip, `interruption` by sim, recovery asserts |
| Silence / user hold | `silence_after_cue_ms`, dead_call guard while scripted silence |
| Forensic log | `events.jsonl`, timeline, summary, SQLite |
| Optional LLM judge | `PassCriteria` |
| Local stereo audio | `conversation.wav` (L=sim, R=agent) |
| Web replay + markers | barge / silence / recovery highlight |
| Batch + compare | `execute-all` suite matrix, `compare` (thin — no golden baseline) |
| Scaffold / guide | `scenario-init`, `guide`, `install.sh`, `lk-sim cues` |
| CLI ↔ MCP parity | Single `ops` surface |

P0 “talk like a person on one call” ≈ **done for v1**.

---

## P0 — “Talk more like a real person” (closed for v1)

| Gap | Status | Notes |
|---|---|---|
| Diverse personas | **Done (v1)** | `Persona.traits[]` + constraints |
| Parallel speech+noise | **Done** | Mixer |
| Interruption / barge-in | **Done (v1)** | Still polish: wider vocal WAV library |
| Silence / user gone | **Done** | 20s hold + dead_call guard |
| Outcome / tool / recovery asserts | **Done** | including optional recovery timing |
| Multi-repo cues | **Done** | builtin + target override |
| Caller pattern redesign | **Done (v1)** | `docs/caller-pattern-plan.md` |

**P0 residual polish (optional, not blockers):**

- Broader Vietnamese / EN **speech** WAV pack (variants of wait / sorry / backchannel)
- Optional `barge_in.auto_blip: false` when using vocal PCM (partially via voice-asset detection)
- `noise_gain` / SNR levels as first-class `speech_conditions`

---

## P1 — Daily QA / regression loop ← **do next**

Aligned with LiveKit/Hamming “regression + offline eval” without becoming SaaS.

| Priority | Gap | Why (from research) | Candidate work | Status |
|---|---|---|---|---|
| **P1.1** | Suite report + CI gate | Automated regression is table stakes | `execute-all` → suite matrix + `suite-*.json/md` | **Done (v1)** |
| **P1.5** | Exit-code policy | CI needs hard gates | hard: status/assert/script; judge soft unless `--strict-judge` | **Done (v1)** |
| **P1.3** | **Voice metrics hard** | Latency is what humans “feel”; competitors gate on P95/TTFW | `summary.metrics` + Assert type `latency` + suite columns (p50/p95/ttfw) | **Done (v1)** |
| **P1.2** | **Flake control (pass@k)** | Gemini caller is stochastic | `execute --repeat N --pass-at-k K` + suite column + MCP | **Done (v1)** |
| **P1.4** | **Fail → golden** | Hamming/Coval flywheel: fail becomes permanent case | `lk-sim scenario-from-run <run-id>` draft JSONL from transcript + script markers | **Done (v1)** |
| **P1.7** | **Hard hangup** | `hangup_threat` is prompt-only; real callers hang up | Script/Behavior `hang_up` + Assert ended_by | **Done (v1)** |
| **P1.6** | Text-fast mode | Cheap loop before full voice | Optional text path (later if suite cost hurts) | Deferred |

### Suggested ROI order

```text
1. ~~P1.3 metrics pack + latency Assert + suite columns~~ Done v1
2. ~~P1.2  --repeat / pass@k~~ Done v1
3. ~~P1.4  scenario-from-run~~ Done v1
4. ~~P1.7  hang_up action + end-call assert~~ Done v1
5. P0 polish  vocal pack + noise_gain  ← next
```

---

## P2 — Scale / production-adjacent (defer)

| Gap | Notes | Competitors |
|---|---|---|
| Accent matrix | Multi-locale / multi-voice packs; stay trait + locale for now | Hamming, Coval |
| Concurrent / load rooms | Use `lk perf agent-load-test`; **no Gemini N-way** | Hamming, Cekura |
| SIP / telephony (inbound / outbound) | Research done; handle via optional telephony harness / docs recipe, not provider hardcoding in core | Cekura, Hamming, Coval |
| Prod import / shadow replay | Online eval SaaS territory | Hamming Observe, Coval |
| Auto-gen scenarios from SOP | Nice-to-have | Future AGI, Phonely |
| Multi-party handoff | LiveKit multi-agent workflows | — |
| Audio-native eval (WER, prosody) | Transcript + judge only today | Okareo, Hamming |
| OTel export from lk-sim | Optional bridge to agent observability | — |

---

## Developer manual work vs lk-sim

| Manual developer | lk-sim today |
|---|---|
| Open mic, greet agent | ✅ |
| Happy-path flow | ✅ |
| Barge-in, silence, noise | ✅ (PCM + vocal cues) |
| Switch language / voice | ⚠️ config — no scenario matrix |
| Check tool calls | ✅ Assert.tools |
| Check interruption recovery | ✅ Assert recovery (+ timing) |
| Re-listen | ✅ wav + web |
| Fail CI on slow turns | ✅ Assert `latency` (hard via assert_verify) |
| Compare before/after | ⚠️ compare — no golden baseline |
| Stable pass under flake | ✅ `--repeat N --pass-at-k K` (hard via assert_verify) |
| Promote bug to regression | ✅ `scenario-from-run <run-id> [--write]` (draft then review) |
| Sim caller hard hangup | ✅ Script `hang_up` action + Assert `ended_by` |
| Run 20 cases before ship | ✅ execute-all suite matrix + hard gate (judge soft) |
| Stable pass under flake | ✅ `--repeat N --pass-at-k K` (hard via assert_verify) |
| Promote bug to regression | ✅ `scenario-from-run <run-id> [--write]` (draft then review) |
| Inbound / outbound SIP call | ⚠️ possible via target telephony harness; lk-sim observes resulting LiveKit room + SIP participant |
| Load 100 concurrent | ❌ |

---

## Suggested order (post-research)

1. ~~Behavioral richness (P0)~~ **Done v1**
2. ~~Hard asserts + recovery~~ **Done**
3. ~~**P1.1 + P1.5** — suite matrix + CI exit policy~~ **Done v1**
4. ~~**Caller pattern redesign (Hamming-aligned)**~~ **Done v1** — `docs/caller-pattern-plan.md`
5. ~~**P1.3** — metrics hard~~ **Done v1** (`metrics.py`, Assert `latency`, suite columns)
6. ~~**P1.2** — pass@k~~ **Done v1** (``execute --repeat --pass-at-k``, MCP, CLI)
7. ~~**P1.4** — scenario-from-run~~ **Done v1** (``scenario-from-run <run-id>``, MCP, draft JSONL)
8. ~~**P1.7** — hard hangup~~ **Done v1** (Script action hang_up + Assert ended_by + CLI/MCP)
9. Later: telephony harness recipe; load via `lk perf` (not Gemini N-way); accent packs if needed

Keep portable: no consumer keys in `src/`; extend via scenario / `.agent-sim/cues` / config / verify plugins (`AGENTS.md`).

---

## Explicit non-goals (for now)

- Replacing LiveKit **in-process** Agents pytest / FakeActions session tests
- Full Hamming/Coval-class production monitoring SaaS
- 50k concurrent simulation
- Built-in neural accent models
- Domain hardcoding one monorepo’s business into core
- Owning telephony providers — lk-sim should not provision trunks, phone numbers, billing, compliance, or target-specific Twilio/LiveKit credentials in core

---

## Status

| Area | Status |
|---|---|
| Core sim + report + web replay | Done |
| P0 behavior + structured pass + cue catalog | **Done (v1)** |
| Caller character (constraints / Behavior / recovery) | **Done (v1)** |
| P1.1 suite + P1.5 exit gate | **Done (v1)** |
| P1.3 latency metrics hard | **Done (v1)** |
| P1.2 pass@k | **Done (v1)** |
| P1.4 fail → golden | **Done (v1)** |
| P1.7 hard hangup | **Done (v1)** |
| P2 load / accent / prod observe | Deferred |
| P2 SIP outbound | Researched; implement O1–O3 next |
| P2 SIP inbound | After outbound |

Update this file when gaps close or priorities change.

---

## Research note (2026-07-13) — telephony (unified SimLeg design)

Sources: LiveKit [`/telephony/making-calls/outbound-calls/`](https://docs.livekit.io/telephony/making-calls/outbound-calls/), [`workflow-setup`](https://docs.livekit.io/telephony/making-calls/workflow-setup/), [`testing`](https://docs.livekit.io/telephony/testing/), [`sip-participant`](https://docs.livekit.io/reference/telephony/sip-participant/); internal `adapter.py`, `run_orchestrator.py`, `scenario.py`, `gemini/live_session.py`, `AGENTS.md`; reference only: `voice-ai-worker/scripts/outbound.ts` (target pattern, **not** core contract).

### Product rule (corrected)

**Gemini always = simulated human** (thay người test). Persona / Script / Behavior / mixer / judge **không đổi** giữa các mode.

Chỉ **đường audio** (transport leg) khác — agent luôn nghe “người” qua WebRTC hoặc SIP tùy topology.

| Topology | Gemini đóng vai | Agent đóng vai | Media leg của sim |
|---|---|---|---|
| **WebRTC** (`webrtc_sim`) — **hiện tại** | Người gọi vào (caller) | Agent nhận cuộc | WebRTC `lk-sim-caller` publish mic |
| **Inbound SIP** (`inbound_sip`) | Người gọi PSTN (caller) | Agent nhận cuộc | SIP participant (inbound) ↔ Gemini bridge |
| **Outbound SIP** (`outbound_sip`) | Người nhấc máy (callee) | Agent gọi ra | SIP participant (outbound dial) ↔ Gemini bridge |

**Log / report / assert:** cùng pipeline — `events.jsonl`, `summary.json`, WAV, `Observer`, `Assert`, `PassCriteria` judge. Không có nhánh “observe-only không Gemini”.

### Design pattern — 3 tầng tách biệt (Strategy)

```text
┌─────────────────────────────────────────────────────────────┐
│  RunOrchestrator (template method — giữ 7 phase hiện tại)    │
├─────────────────────────────────────────────────────────────┤
│  1. RoomLifecycle    create_room_and_dispatch + wait_agent   │  ← chung
│  2. SimLeg.connect  webrtc | inbound_sip | outbound_sip     │  ← strategy
│  3. SimBrain         GeminiCallerBridge + ScriptRunner       │  ← chung
│  4. ObserveReport    Observer + EventWriter + recorder       │  ← chung
│  5. VerifyJudge      asserts + script_verify + judge        │  ← chung
└─────────────────────────────────────────────────────────────┘
```

**`SimLeg` protocol** (new, replaces hard-coded `connect_simulator` only):

```python
class SimLeg(Protocol):
    async def connect(self, adapter, room_name, run_spec) -> SimLegHandle: ...
    # handle exposes: room (for Observer), sim_identity, agent_audio_subscribe, gemini_bridge hook

class WebRtcSimLeg:      # today — lk-sim-caller WebRTC
class OutboundSipSimLeg:  # CreateSIPParticipant + Gemini↔SIP audio bridge
class InboundSipSimLeg:   # originate inbound call / SIP ingress + Gemini↔SIP bridge
```

**Điểm chung (không copy 3 lần):**

| Layer | Shared today | Telephony adds |
|---|---|---|
| `GeminiCallerBridge` | Persona prompt, TTS, agent audio in, Script, mixer | Same brain; leg feeds agent audio from SIP track not WebRTC agent sub |
| `Observer` | transcript, tools, latency, barge | `sim_identity` = SIP participant identity when on SIP leg |
| `run_orchestrator` phases | prepare → dispatch → connect → converse → verify → judge → finalize | Phase 3 calls `SimLeg` factory from `Caller.mode` |
| Scenario | Persona, Execute, Assert, Dispatch | + `Caller`, + `Telephony` (dial/ingress params) |
| Config | `livekit.*`, `simulator.*` | + `telephony.*` defaults (trunk, prepare_ms) — **no mode** |

**Factory:** `Caller.mode` → `SimLeg` implementation. Default missing `Caller` → `WebRtcSimLeg` (backward compatible).

### Per-mode sequence (only leg differs)

**WebRTC (today):**

```text
dispatch → wait_agent → WebRtcSimLeg.connect → Gemini talks on WebRTC → observe
```

**Outbound SIP (Gemini = callee):**

```text
dispatch → wait_agent → [prepare_ms] → OutboundSipSimLeg.dial → sip.active
→ Gemini speaks as callee on SIP leg → observe
```

**Inbound SIP (Gemini = caller):**

```text
dispatch → wait_agent → InboundSipSimLeg.originate_inbound → sip.active
→ Gemini speaks as PSTN caller on SIP leg → observe
```

`first_speaker` in `Execute` still applies per topology (outbound: often `user` = callee/Gemini speaks first).

### LiveKit outbound — 2 patterns (pick one per target)

| Pattern | Who dials PSTN | Sequence | lk-sim role |
|---|---|---|---|
| **A. Harness-initiated** (recommend v1) | lk-sim / test script via `CreateSIPParticipant` | 1) create room 2) dispatch agent 3) wait agent ready 4) dial callee | lk-sim owns dial + observe |
| **B. Agent-initiated** | Agent reads opaque `job.metadata` and calls `create_sip_participant` in entrypoint | 1) dispatch agent with metadata 2) agent dials inside worker | lk-sim only dispatches + observes; dial logic stays in target agent |

LiveKit canonical API ([outbound calls doc](https://docs.livekit.io/telephony/making-calls/outbound-calls/)):

```text
CreateSIPParticipant(
  sip_trunk_id | inline trunk,
  sip_call_to,          # E.164 destination
  room_name,
  participant_identity,
  wait_until_answered,
  krisp_enabled,
  ...
)
→ SIP participant joins room → sip.callStatus: dialing → ringing → active
```

**Trunk modes (user-owned config, not hardcoded):**

| Mode | When | User provides |
|---|---|---|
| **Stored trunk** | Most setups | `sip_trunk_id` from `lk sip outbound list` |
| **Inline trunk** | Multi-tenant / per-call provider | `trunk.hostname`, auth, `sip_number` (from-number) |

lk-sim must support **both** via config — no Twilio/Telnyx-specific branches in `src/`.

### WebRTC vs SIP legs (same Gemini, different wire)

| | `webrtc_sim` (today) | `inbound_sip` | `outbound_sip` |
|---|---|---|---|
| Gemini role | Caller | Caller (PSTN) | Callee (answers phone) |
| Sim media | WebRTC publish | SIP ↔ bridge | SIP ↔ bridge |
| Persona / Script | ✅ | ✅ | ✅ |
| `google_api_key` | ✅ required | ✅ required | ✅ required |
| Stereo WAV | L=sim R=agent | L=sim(SIP) R=agent | L=sim(SIP) R=agent |
| Extra events | — | `sip.inbound_*` | `outbound.dial_*` |

**Open engineering (SIP legs):** Gemini↔SIP audio bridge — options ranked for v1 research: (1) dial/answer via lk-sim-controlled SIP endpoint + WebRTC bridge in same process; (2) LiveKit participant forwarding; (3) external softphone. Pick one in O2 spike — **do not** ship observe-only PSTN without sim.

### Generic split: lk-sim core vs user custom

| Concern | lk-sim core (common tool) | User custom (target `.agent-sim/`) |
|---|---|---|
| LiveKit room + agent dispatch | ✅ reuse `LiveKitAdapter.create_room_and_dispatch` | `livekit.agent_name`, `room_prepare_ms` |
| Opaque agent config | ✅ pass through only | `Dispatch.metadata` JSON string (any keys — `customAgentId`, `phone_number`, …) |
| SIP trunk + dial / ingress | ✅ `SimLeg` + `CreateSIPParticipant` / inbound originate | `config.telephony.*` defaults; scenario `Telephony.*` overrides |
| Phone / routing target | ✅ read from scenario | **`Telephony.call_to`** (outbound) or **`Telephony.dial_in`** (inbound) |
| Prepare delay before dial | ✅ merge config ← scenario | `Telephony.prepare_ms` overrides `config.telephony.prepare_ms` |
| Wait for answer / catch SIP errors | ✅ `wait_until_answered`, log `sip_status_code` | per-scenario override |
| Observe transcript/tools/latency | ✅ existing `Observer` + asserts | `observe.*`, plugins |
| Agent business logic / when to dial | ❌ never parse metadata keys | target agent code (pattern B only) |
| Trunk provisioning / Twilio console | ❌ | user / ops |
| Billing, compliance, caller ID policy | ❌ | user |

**Rule (from `AGENTS.md`):** core never parses `customAgentId`, `phone_number`, etc. — only forwards `Dispatch.metadata` as opaque string. If agent-initiated outbound needs `phone_number` in metadata, user writes that JSON; lk-sim does not interpret it.

### Config vs scenario — who owns what

**Principle:** shared `config.yaml` = credentials + infrastructure defaults only. **Scenario JSONL** decides *what kind of call this run is* (WebRTC sim vs outbound SIP vs agent dials).

Same pattern as today: `Execute` overrides `Simulator`; `Dispatch.metadata` overrides `livekit.dispatch_metadata`. Outbound follows that — **no `mode` in config**.

| Layer | Owns | Examples |
|---|---|---|
| **`config.yaml`** | Secrets + optional telephony **defaults** | `livekit.*`, `simulator.*`, `telephony.sip_trunk_id`, `telephony.prepare_ms` |
| **Scenario `Caller`** | **SimLeg mode** (per test) | `mode: webrtc_sim` \| `inbound_sip` \| `outbound_sip` |
| **Scenario `Telephony`** | Per-run SIP params | `call_to`, `dial_in`, `wait_until_answered`, trunk override |
| **Scenario `Dispatch`** | Per-run opaque metadata | `metadata` JSON string |
| **Scenario `Execute`** | Per-run timing / first speaker | `timeout_s`, `first_speaker: user` for outbound |

**Default:** no `Caller` line → `webrtc_sim` (all existing scenarios unchanged).

### Proposed config / scenario contract (portable)

**`config.yaml`** — shared, no mode:

```yaml
livekit:
  url: wss://...
  api_key: ...
  api_secret: ...
  agent_name: your-agent-name
  dispatch_metadata: '{"yourOpaqueKey":"default"}'   # optional default

# Optional telephony defaults (not mode!)
telephony:
  sip_trunk_id: ST_xxxx
  prepare_ms: 3000
  wait_until_answered: true
  krisp_enabled: false
```

**Scenario JSONL** — mode + per-run telephony:

```jsonl
{"kind":"Scenario","spec":{"id":"outbound-customer-sim","tags":["telephony","outbound"]}}
{"kind":"Caller","spec":{"mode":"outbound_sip"}}
{"kind":"Persona","spec":{"name":"Busy customer","brief":"You answered an unknown call. Be skeptical, short answers."}}
{"kind":"Telephony","spec":{"call_to":"+84901234567"}}
{"kind":"Dispatch","spec":{"metadata":"{\"customAgentId\":\"agent_xxx\"}"}}
{"kind":"Execute","spec":{"timeout_s":120,"first_speaker":"user","max_turns":10}}
```

Inbound SIP example:

```jsonl
{"kind":"Scenario","spec":{"id":"inbound-support","tags":["telephony","inbound"]}}
{"kind":"Caller","spec":{"mode":"inbound_sip"}}
{"kind":"Persona","spec":{"name":"Caller with billing question"}}
{"kind":"Telephony","spec":{"dial_in":"+18005551234"}}
{"kind":"Execute","spec":{"first_speaker":"user","max_turns":8}}
```

WebRTC scenario (unchanged — no `Caller` line):

```jsonl
{"kind":"Scenario","spec":{"id":"smoke-hello","tags":["smoke"]}}
{"kind":"Persona","spec":{"name":"Friendly caller"}}
{"kind":"Execute","spec":{"max_turns":6,"timeout_s":120}}
```

**Merge rules:**

```text
effective_mode       = Caller.mode              ?? "webrtc_sim"
effective_sim_leg    = factory(Caller.mode)     # WebRtcSimLeg | InboundSipSimLeg | OutboundSipSimLeg
effective_call_to    = Telephony.call_to        ?? (required if outbound_sip)
effective_dial_in    = Telephony.dial_in        ?? (required if inbound_sip)
effective_trunk      = Telephony.sip_trunk_id   ?? config.telephony.sip_trunk_id
effective_prepare_ms = Telephony.prepare_ms     ?? config.telephony.prepare_ms
```

`Caller` / `Telephony` are new optional kinds — not required for existing WebRTC scenarios.

### Events + asserts lk-sim should add (common)

| Event | When |
|---|---|
| `outbound.dial_started` | before `CreateSIPParticipant` |
| `outbound.dial_answered` | `sip.callStatus=active` or API success with `wait_until_answered` |
| `outbound.dial_failed` | TwirpError + `metadata.sip_status_code` |
| `sip.participant_connected` | room event, `kind=SIP` |

| Assert (optional) | Meaning |
|---|---|
| `sip_call_status` | expect `active` within N ms |
| `sip_participant_present` | exactly one SIP callee in room |
| existing `latency`, `recovery`, `tools`, `ended_by` | reuse on telephony room |

SIP attributes to surface in report ([sip-participant doc](https://docs.livekit.io/reference/telephony/sip-participant/)): `sip.callStatus`, `sip.phoneNumber`, `sip.trunkPhoneNumber`, `sip.trunkID`, `sip.callID`, `sip.twilio.callSid` (if Twilio trunk — read-only, no special case in core).

### Implementation phases (outbound only)

| Phase | Deliverable | Risk |
|---|---|---|
| **T0** | Extract `WebRtcSimLeg` from `connect_simulator` (refactor only, no behavior change) | Low |
| **T1** | `SimLeg` protocol + factory from `Caller.mode`; docs `telephony.md` | Low |
| **T2** | `OutboundSipSimLeg` + Gemini↔SIP bridge spike + `CreateSIPParticipant` | High |
| **T3** | `InboundSipSimLeg` + originate/ingress spike | High |
| **T4** | SIP asserts (`sip_call_status`, `sip_participant_present`) + suite columns | Low |
| **T5** | Pattern B only: `agent_dials` (dispatch observe; agent owns dial) | Low |

**Defer:** inline trunk UI, DTMF script actions, Twilio Connector path, automated load on PSTN.

### Anti-patterns (do not copy into core)

| Anti-pattern | Why |
|---|---|
| Hardcode `customAgentId` / dashboard env in `src/` | Target-specific; belongs in `Dispatch.metadata` |
| Parse dispatch metadata for business keys | Violates opaque dispatch rule |
| Port `voice-ai-worker/scripts/outbound.ts` verbatim | Good reference, wrong layer — becomes config + adapter |
| Observe-only outbound (no Gemini) | Violates product rule — Gemini always replaces the human on the wire |
| Duplicate converse/observe logic per mode | Use `SimLeg` strategy; one `run_orchestrator` template |
| Put `mode` in shared `config.yaml` | Mode is per-test; belongs in scenario `Caller`, like `Execute` vs `Simulator` |

### Open questions (owner)

1. **SIP bridge v1:** lk-sim-controlled SIP endpoint vs media forward — spike in T2 decides
2. **CI telephony:** shared test trunk + sim endpoint (no human handset)
3. **Ship order:** T0 refactor → T2 outbound → T3 inbound (or parallel after T1)

### Status

| Item | State |
|---|---|
| Telephony unified design (SimLeg) | **Done** |
| Inbound + outbound research | **Done** (same pattern) |
| Core implementation | Not started — reply **go ahead** to implement T0+T1+T2 |

## Telephony SimLeg (2026-07-13)

Implemented portable SimLeg: webrtc_sim / inbound_sip / outbound_sip / agent_dials. See docs/telephony.md and docs/plans/PLAN-20260713-telephony-simleg.md.
