# Plan: Caller pattern redesign (Hamming-aligned, room-native)

**Status:** implemented (v1) — Phases A–E  
**Date:** 2026-07-11 · **Shipped:** 2026-07-12  
**Scope:** Redesign the **simulated caller** model so it matches Hamming-style *character + behavior + assertions*, while **keeping LiveKit room + black-box agent**.  
**Out of scope:** concurrent load rooms, multi-agent handoff, SIP (unless noted as later).

---

## 1. Research summary

### 1.1 Hamming Caller model

Sources: [Hamming LiveKit integration](https://hamming.ai/integrations/livekit), [persona template](https://hamming.ai/resources/voice-agent-test-personas-support-calls-template), [tests-as-code YAML](https://hamming.ai/resources/voice-agent-tests-as-code-template), [interruption runbook](https://hamming.ai/resources/voice-agent-interruption-handling-runbook).

| Pillar | Hamming practice |
|---|---|
| Transport | LiveKit-to-LiveKit WebRTC room (or SIP/phone) — **same geometry as lk-sim** |
| Caller | **Voice character**, not a freeform chat bot |
| Structure | Separate **Persona / Scenario / Fixture / Assertion / Evidence** |
| Behavior | Interrupts, silence, noise, accents, emotional temperature as **first-class** |
| Pass/fail | Outcome + tool + recovery (+ latency), not transcript prose alone |
| Lifecycle | Pattern from prod → redacted persona → CI gate |

Hamming persona quality rule: *if two agents pass while taking different workflows, the persona/asserts are underspecified.*

### 1.2 lk-sim today (gap)

| Hamming field | lk-sim today | Gap |
|---|---|---|
| `caller_goal` | `Persona.goals[]` + brief | OK but unstructured |
| `communication_style` | `style` + `traits[]` | OK v1; no constraints |
| `constraints` (won't share card, hang up if…) | Missing | **High** |
| Behavior policy (barge/silence/noise as character) | Script steps **manual** | Script not generated from persona |
| Speech conditions (accent, noise) | Partial cues + mixer | No vocal pack / speech_conditions |
| Fixtures | Target-only (DB seed) | OK portable — document only |
| Assertions layered | Assert + PassCriteria + script_verify | Recovery/latency typed weakly |
| Evidence | events + wav + web | Good; gate already hard on assert/script |
| Prod → test | None | P1.4 later |

**Keep:** room, dispatch opaque, Gemini Live, Script runner, ParallelMicMixer, cue catalog, suite CI gate, web markers.

**Redesign:** **Caller as Character** — one coherent model that drives prompt + optional script defaults + asserts.

---

## 2. Target architecture

```text
                    ┌─────────────────────────────────────┐
                    │  Scenario JSONL (agent-sim/v1)        │
                    │  Persona + Behavior + Asserts + Script │
                    └──────────────┬──────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
   persona_system_prompt    Script steps (timed)     evaluate_* (hard)
   (Gemini Live brain)      barge/silence/pcm        assert + script_verify
           │                       │                       │
           └───────────┬───────────┘                       │
                       ▼                                   │
              ParallelMicMixer ──► LiveKit room            │
              (speech + noise layers)                      │
                       │                                   │
                       ▼                                   ▼
              events.jsonl + conversation.wav ◄──── suite gate / web
```

**Principle:** *Dialog policy* (what to say) stays LLM; *interaction policy* (when to cut in / go silent / inject audio) stays **deterministic Script** (or auto-derived Script), so QA is replayable.

---

## 3. Target scenario model (v2 additive)

Stay on **agent-sim/v1** JSONL kinds; extend `Persona.spec` and optional new kind. No break of existing files.

### 3.1 Persona (character) — extend `Persona.spec`

```json
{
  "kind": "Persona",
  "spec": {
    "name": "Lan",
    "language": "vi-VN",
    "brief": "…",
    "goals": ["…"],
    "style": "…",
    "traits": ["impatient", "interrupts"],
    "constraints": [
      "Will not share card numbers over the phone",
      "Will hang up if asked to restart from the main menu"
    ],
    "speech_conditions": {
      "noise": "builtin:noise.ambient",
      "noise_when": "background",
      "barge_policy": "mid_agent_turn",
      "silence_ms": 0
    }
  }
}
```

| Field | Required | Maps to |
|---|---|---|
| `brief` / `goals` / `style` / `traits` | as today | system prompt |
| `constraints[]` | no | system prompt **hard rules** + hangup detectors later |
| `speech_conditions` | no | **compiler** → default Script steps if Script absent/partial |

### 3.2 Optional kind `Behavior` (explicit, Hamming “policy”)

Prefer explicit over magic when author wants control:

```json
{
  "kind": "Behavior",
  "spec": {
    "barge_ins": [
      {"id": "cut1", "after_agent_ms": 800, "say": "Khoan đã!", "delivery": "gemini_text", "with_blip": true}
    ],
    "user_silence": [
      {"id": "pause20", "hold_ms": 20000, "after": "first_user_goal"}
    ],
    "ambient": {"asset": "builtin:noise.ambient", "when": "after_join_ms", "delay_ms": 5000}
  }
}
```

Compiler expands → existing `ScriptStep` list (single runner path).

### 3.3 Assertions (typed, layered)

Keep `Assert` + `PassCriteria`; add recovery-friendly outcomes:

| type | Meaning | Hard? |
|---|---|---|
| `transcript_contains` | exists today | hard |
| `tools` | exists today | hard |
| `recovery` | min agent finals after barge / interruption | hard (script_verify or assert) |
| `llm_bool` / PassCriteria | soft unless `--strict-judge` | soft |
| `latency` (later) | turn_taking p95 max | hard optional |

### 3.4 Fixtures (portable boundary)

**Core does not seed DBs.** Document only:

```json
{"kind": "Context", "spec": {"notes": "…", "fixtures": {"customAgentId": "…", "hint": "dashboard seed order_017"}}}
```

`fixtures` are **opaque notes for humans/tools**; dispatch still only via `Dispatch.metadata`.

---

## 4. Implementation phases

### Phase A — Character schema + prompt (1–2 days)

**Goal:** Hamming-shaped persona without breaking old JSONL.

| Task | Files |
|---|---|
| Parse `constraints[]`, `speech_conditions` on Persona | `scenario.py`, parse path |
| Inject constraints into `persona_system_prompt()` | `scenario.py` |
| Expand trait library (backchannel, hangup_threat, code_switch soft) | `persona_traits.py` |
| Scaffold + GUIDE examples | `templates/scenario-scaffold.jsonl`, `GUIDE.md` |
| Unit tests | `tests/test_persona_*.py` |

**Done when:** old scenarios still parse; new fields appear in system prompt dump/export.

---

### Phase B — Behavior compiler → Script (2–3 days)

**Goal:** Character policy becomes timed Script automatically.

| Task | Detail |
|---|---|
| `behavior_compile.py` | `Behavior` + `speech_conditions` → `list[ScriptStep]` |
| Merge rules | Explicit `Script` wins; Behavior fills gaps (or merge by id) |
| Defaults | `barge_policy: mid_agent_turn` → 1–2 barge_in steps; `silence_ms` → wait hold |
| Ambient | `noise` asset → time-based room_pcm |
| `with_blip` | bool on barge (default true for gemini_text; false if vocal WAV) |
| Tests | pure unit compile snapshots |

**Done when:** a scenario with **only Persona.speech_conditions + Assert** runs barge/silence without hand-written Script.

---

### Phase C — Vocal caller audio (2 days)

**Goal:** sound like a person, not only synthetic hiss.

| Task | Detail |
|---|---|
| Package cues | Optional short **speech** WAVs (en + vi if available): `barge_wait`, `barge_sorry`, `backchannel_uhhuh` — or document “drop your own in `.agent-sim/cues/`” |
| Catalog aliases | `voice.barge_short`, `voice.backchannel` |
| Config | `barge.auto_blip: true|false` (default true for text, false if asset is voice.*) |
| Scenario examples | `agent-interrupt-barge` uses vocal asset |

**Done when:** demo scenario barge is **intelligible speech** (or documented target WAV) on L channel mid-agent.

---

### Phase D — Recovery + timing asserts (1–2 days)

**Goal:** Hamming-style “interruption recovery” measurable.

| Task | Detail |
|---|---|
| Assert type `recovery` | `min_interruptions`, `min_agent_finals_after_barge_in` already in script_verify — unify UX under Assert or keep script_verify |
| Timing | `max_ms_after_barge_to_agent_final` from events |
| Summary field | `caller.behavior_summary` (barges fired, silences held, during_agent counts, `recovery_ms`) |
| Web | recovery chip (+ barges / silence holds) from summary + assert recovery outcomes |

**Done when:** CI hard-fails if barge fired but agent never re-engages (already partial); optional latency gate works.

---

### Phase E — Authoring UX (1 day)

| Task | Detail |
|---|---|
| `scenario-init` | Hamming-style commented template (goal, constraints, speech_conditions, asserts) |
| `lk-sim guide` section | “Caller character (Hamming-aligned)” |
| Example suite | `templates/examples/character-impatient.jsonl` (neutral domain) |

---

### Phase F — Later (not this redesign core)

| Item | Note |
|---|---|
| `scenario-from-run` | Prod/fail → draft persona (P1.4) |
| Metrics pack TTFW suite columns | P1.3 |
| pass@k | P1.2 |
| Accent matrix / 65 languages | SaaS-scale; we stay trait + language locale |
| Load concurrent | Use `lk perf`; no Gemini N-way |

---

## 5. Migration & compatibility

| Rule | |
|---|---|
| API version | Stay `agent-sim/v1` |
| Old Persona | Still valid |
| Old Script | Still valid; highest priority when present |
| Behavior + Script | Document merge order |
| CI gate | Unchanged: hard assert/script/status; soft judge |

---

## 6. Success criteria

1. Author can define a **character** (goal + constraints + barge/silence/noise intent) without writing 10 raw Script lines.  
2. Mid-call **cut-in is audible** (vocal or blip+text) and marked in events/web.  
3. Hard asserts prove **outcome / tool / recovery**, not only “agent said hello”.  
4. Zero consumer hardcoding in `src/`.  
5. Existing voice-ai-worker scenarios still run.  

---

## 7. Suggested implementation order (execution)

```text
A schema+prompt  →  B behavior compile  →  C vocal cues  →  D recovery timing  →  E scaffold/docs
```

**First PR (minimal valuable):** Phase A + thin Phase B (only `constraints` + auto 1 barge from `speech_conditions.barge_policy`) + one example scenario.

**Effort estimate:** ~1 week focused for A–E; F deferred.

---

## 8. Explicit non-goals

- Replace LiveKit in-process pytest  
- Hamming-scale concurrent characters  
- Built-in accent neural models  
- Core parsing of `customAgentId` / business fixtures  

---

## 9. References

- Hamming persona template: https://hamming.ai/resources/voice-agent-test-personas-support-calls-template  
- Hamming tests-as-code: https://hamming.ai/resources/voice-agent-tests-as-code-template  
- Hamming LiveKit: https://hamming.ai/integrations/livekit  
- LiveKit official testing (in-process): https://docs.livekit.io/agents/start/testing/  
- This package: `AGENTS.md`, `WIP.md`, `docs/portability.md`
