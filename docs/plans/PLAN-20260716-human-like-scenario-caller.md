# Plan Report

## Summary (read this first)
- **You asked:** Go deeper — make `lk-sim` feel like a **real human caller**, driven by the scenario, then develop the conversation with the agent (Exa research OK). Follow-up: **research thêm kỹ**.
- **What is going on:** Today we mix three jobs in one Gemini Live session: (1) Persona freestyle, (2) Script timing/barge fixtures, (3) midcall text that accidentally **triggers speech**. Industry tools split these: **goal/persona-driven caller** vs **deterministic interaction fixtures**.
- **We recommend:** Evolve toward a **Persona-led dialogue mode** (situation + goals + constraints + outcome) as the default “human call,” and keep **Script** as an optional **interaction overlay** (noise / barge WAV / forced fee ask) — not as the only mouth of the caller. Fix bootstrap (no speak-inducing text) first.
- **Risk:** Medium (product split of modes; scenario authoring changes). Low if we stage: bootstrap fix → persona-dialogue mode → Script overlays.
- **Status:** Implemented (phases 0–5) — 2026-07-16

## Implementation notes (done)
- **Phase 0:** `DefaultCallerPolicy.midcall_cues` returns **no** `bootstrap` cues; SI (`FirstSpeakerSection`) owns silence / speak-first. `_emit_bootstrap_cues` remains for custom policies only.
- **Phase 1:** `Persona.situation` / `Persona.outcome` in SI + authoring warnings; sample `dialogue-signup-basic.jsonl` (worker + `templates/examples/`).
- **Phase 2:** Dialogue SI rewrite (goals/guardrails/one-time vs loop); Script path framed as overlay.
- **Phase 3:** `ScriptStep.overlay` (`fixture`|`line`) + `effective_overlay()`; soft-barge scenario annotated.
- **Phase 4:** `_mostly_script_say` so concatenated freestyle+open STT stays `natural`, not false `script_cue`.
- **Phase 5:** Dialogue sample Assert outcomes + PassCriteria; `llm_bool` outcome for signup path.

## Feature planning
- **Recommended approach:** Two scenario modes (can coexist in one JSONL):
  1. **`dialogue` (human-like):** Rich Persona + Context + PassCriteria/Assert. Gemini Live **owns speech**. System instruction = Google SI order (persona → conversational rules/goals → guardrails). Opening: wait for agent **or** one SI-driven first turn — **never** `send_realtime_input` bootstrap that says “stay silent.”
  2. **`interaction` (lab fixtures):** Script owns exact timing (PCM barge, noise, soft-barge gain, hang_up gates) for VAD/AAD tests. Optional forced lines. Freestyle only between cues for answers.
- **Prior art (GitHub / Exa):** see **Deep research addendum** below.
- **Integration points:** `caller/default_policy.py`, `prompt_sections.py`, `gemini/live_session.py` (`_emit_bootstrap_cues`), scenario schema (`Persona` / `Context` / optional `Dialogue` kind), `vad-rt-*` stay Script-heavy; new `dialogue-*` scenarios for human signup calls.
- **Sub-agents used:** skipped — Exa + LiveKit MCP + code map sufficient
- **Option B:** Keep Script-primary forever; only polish prompts — cheaper but never feels like a real call for fee/name flows.
- **Open questions:** Hang_up in dialogue = Persona `[END_CALL]` + PassCriteria (preferred); Script hang_up only for interaction. Optional later: Coval-style knobs (interruption rate / silent mode) as Script overlays, not SI text.

## Evidence (load-bearing, verified)
1. **Gemini docs:** SI = persona + ordered rules + guardrails; initiating speech needs an explicit start prompt **in SI / setup** — not a conflicting “stay silent” realtime text. ([Live API best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices))
2. **Practitioner gist (Mar 2026):** Mid-session text ≈ user turn → double speech / interrupt. Put everything in SI before connect. ([hayesraffle gist](https://gist.github.com/hayesraffle/ab09fc7d21a5df3a01b0f69fb353280c))
3. **Industry:** Human-like sims are **situation + goals + outcome**; scripted audio is for **voice-native failure modes** (barge, noise). Coval: **persona ≠ test case**.
4. **Our code today:** `default_policy.midcall_cues` emits Script bootstrap via `send_realtime_input(text=…)` → `_emit_bootstrap_cues` — proven double-open path. SI sections already Google-ordered but ScriptTiming fights dialogue goals.

## Target architecture (simple)

```
Scenario
  Persona (who / style / constraints)     ← Coval “persona”
  Context (world facts the caller knows)
  Goals + desired outcome                 ← Coval “test case” / Future AGI situation→outcome
  [optional] Script overlay               ← interaction fixtures only
  Assert / PassCriteria / Judge
```

| Mode | Who speaks | When to use |
|------|------------|-------------|
| Dialogue | Gemini freestyle from SI | “Real signup call”, name/fee Q&A, soft judgment |
| Interaction | Script inject + optional short answers | `vad-rt-*` AAD/VAD, PCM barge, noise blip |
| Hybrid | Dialogue default + 1–2 Script overlays | Soft-barge fee ask mid explanation, then resume freestyle |

## Steps (phased checklist)
1. [x] **Phase 0 — unblock:** Stop Script+user bootstrap via `send_realtime_input` (SI only). Confirms no double-open.
2. [x] **Phase 1 — dialogue scenario shape:** Document `situation` / `outcome` (or reuse Persona.brief + PassCriteria) + richer Context facts; sample `dialogue-signup-basic.jsonl` with **no** Script (or hang_up-only).
3. [x] **Phase 2 — SI rewrite for dialogue:** Google order; goals as checklist; allow 1–2 sentence turns; hang_up policy explicit; no “stay silent between cues” unless Script overlay armed.
4. [x] **Phase 3 — Script as overlay:** When Script present, mark steps `class: fixture` vs forced say; freestyle default; inject only for barge/noise/exact phrases.
5. [x] **Phase 4 — player/taxonomy:** Tag freestyle vs script_inject accurately (fix false “Script cue” on natural speech).
6. [x] **Phase 5 — eval:** Keep Assert + Judge; add outcome-oriented PassCriteria like Future AGI `situation`→`outcome`.

## Files to touch (when implementing)
- `src/livekit_agent_simulator/gemini/live_session.py` — bootstrap
- `src/livekit_agent_simulator/caller/*` — dialogue SI vs script SI
- `scenario.py` / docs — mode docs
- Target `.agent-sim/scenarios/` — new dialogue scenarios; keep `vad-rt-*` as interaction suite

---

## Deep research addendum (2026-07-16)

### A. Industry model — four reusable blocks (Coval)

Verified from [Coval Personas](https://docs.coval.ai/concepts/personas/overview) + [Simulations](https://docs.coval.ai/concepts/simulations/overview) + [Test sets](https://docs.coval.ai/concepts/test-sets/overview):

| Block | Question | lk-sim today | Gap |
|-------|----------|--------------|-----|
| **Agent** | Who is under test? | opaque Dispatch / target worker | OK (boundary) |
| **Persona** | Who is calling / how they sound? | `Persona` kind + traits + voice | Missing knobs: initiator, interruption rate, silent mode (Coval has these as **settings**, not prompt prose) |
| **Test case** | What are they trying to do? | `Persona.goals` + Script lines | Goals often underspecified; Script **steals** the mouth |
| **Metrics** | Did it succeed? | Assert + PassCriteria + Judge | Need clearer **outcome** separate from style |

**Hard Coval rule (adopt):** *Persona = who/how; test case = what. Same test case × many personas.*  
Do **not** bury signup goals only inside Script `say` lines.

**Coval Simulation Input spectrum** (tightness of control):

```
goal / situation  →  conversation_plan  →  exact script lines
     (dialogue)         (hybrid)            (interaction)
```

Our `vad-rt-*` sit at the right end. Human signup calls should sit at the **left**.

**Coval maturity warning (adopt):** LLM sim callers **people-please** — they cooperate too much, don’t stammer, don’t say “uh wait the other thing.” Countermeasures already half-present: `constraints[]`, traits (`impatient`, etc.), Script overlays for forced interruption/noise. Dialogue SI must **positively** encode uncooperative behaviors, not only “be natural.”

### B. Future AGI / LiveKit black-box sim

[Future AGI Simulate SDK](https://docs.futureagi.com/docs/cookbook/simulate-sdk/) against LiveKit:

```python
Persona(name=..., situation="...", outcome="...")
```

- **situation** = caller’s world problem (drives speech).
- **outcome** = success criteria for **eval** (task_completion maps situation → transcript).
- Separate `SimulatorAgentDefinition` for sim customer model/voice.

**Map to lk-sim (minimal schema change):**

| Future AGI | lk-sim |
|------------|--------|
| `situation` | `Persona.brief` **or** new `Persona.situation` (prefer explicit field later) |
| `outcome` | PassCriteria / Assert `llm_bool` / `goals_met` — **not** only Script bye |
| sim voice model | Gemini Live voice config (already) |

### C. Hamming — realism that changes workflow

From Hamming persona template + our `docs/caller-behavior-research.md`:

- Realism ≠ backstory. Realism = **behavior that changes agent path** (interrupt, refuse card, ambiguity, urgency).
- Quality rule: *If two agents can both pass while taking different workflow paths, persona or asserts are too weak.*
- Barge-in is a **typed mid-call input** (correction / backchannel / noise / silence / escalate), not a boolean — maps to Script `class: fixture` overlays.

### D. Gemini Live — non-negotiable wire rules

| Rule | Source | Implication for lk-sim |
|------|--------|------------------------|
| SI order: persona → conversational rules → guardrails | [Google best practices](https://ai.google.dev/gemini-api/docs/live-api/best-practices) | Keep `prompt_sections` order; split dialogue vs script rule blocks |
| One SI per role | same | Caller SI only; never mix agent instructions |
| **Starting commands in SI** to initiate speech | same | User-first → put “greet once” in SI; **do not** send bootstrap realtime text for “speak first” |
| `send_realtime_input(text=…)` = user turn | Google skill + hayesraffle | Bootstrap “stay silent” **speaks** (paraphrase) → double-open |
| Mid-session text interrupts current speech | hayesraffle forum/gist | Never inject conductor text while agent/sim speaking; buffer until turn complete if must |
| Cloud: `role=system` content can update SI mid-session | [GCP Live start-manage](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/start-manage-session) | Optional later for mode switch; **not** default path (AI Studio / genai path may differ — verify in `.venv` before using) |
| Audio ~25 tok/s; 15 min without compression | Google session mgmt | Long dialogue runs need compression if >~15 min |
| Pin language with UNMISTAKABLY | Google | Already in `RoleSection` |

**Verified bug seam in our code:**

```text
DefaultCallerPolicy.midcall_cues
  → kind=bootstrap "Stay completely silent until SIMULATOR CUE…"
  → live_session._emit_bootstrap_cues
  → session.send_realtime_input(text=…)
  → Gemini treats as user turn → speaks paraphrase
  → Script open inject speaks real line
  → double opening (run barge-pcm …d727)
```

Phase 0 fix: **omit bootstrap MidcallCue entirely** when Script owns first speech; for user-first dialogue, put start command in SI (`FirstSpeakerSection` already nearly does this).

### E. LiveKit official stance

[LiveKit testing docs](https://docs.livekit.io/agents/start/testing/): built-in helpers are **text-only** AgentSession evals; audio E2E is **third-party** (Hamming, Coval, Cekura, Bluejay).  

[IVR navigator recipe](https://docs.livekit.io/reference/recipes/ivr-navigator/): agent role-plays **human caller with a task goal** — goal in instructions, not a line script. Same pattern we want for dialogue mode.

**Boundary (keep):** lk-sim = black-box room caller; do not replace worker pytest.

### F. GitHub prior art — what to reuse / avoid

| Project | Reuse | Avoid |
|---------|-------|-------|
| [PersonaForge](https://github.com/arjun-vegeta/PersonaForge) | Goal-driven customers + memory/subgoals + separate Judge | ElevenLabs-only runner; SaaS scale |
| [voice-agent-lab](https://github.com/lacocayxanh025-png/voice-agent-lab) | Persona fields: objections, patience, interruption, stop conditions, forbidden content | Text-first core as product default |
| [USP / arXiv 2502.18968](https://github.com/wangkevin02/USP) | Implicit traits > “As a father of two” explicit dump | Training a custom user-sim model (out of scope) |
| [BargeKit](https://github.com/rogerchappel/bargekit) | Typed barge fixtures for regression | Replacing Gemini Live VAD with another stack in core |
| LiveKit IVR navigator | Goal string drives caller behavior | DTMF-specific as dialogue default |

### G. What “human-like” means for us (acceptance criteria)

After Phase 0–2, a `dialogue-signup-basic` run should show:

1. **One** opening (agent greets XOR caller greets) — no Gemini paraphrase before Script.
2. Caller **answers** name / plan / fee questions in freestyle Gemini voice (same timbre as Script inject after Gemini-first TTS fix).
3. Conversation **advances goals** without needing a Script line for every turn.
4. Call ends via `[END_CALL]` or agent hang when PassCriteria satisfied — Assert/Judge can score **outcome**.
5. Player tags freestyle vs inject correctly.

`vad-rt-*` remain interaction suite: Script fixtures still required for AAD/VAD reproducibility.

### H. Explicit non-goals

- Not becoming text-only chat sim.
- Not parsing consumer Dispatch keys in core.
- Not hard-muting all freestyle again (that killed human Q&A).
- Not cloning Coval SaaS; adopt **schema ideas** only.

### I. Revised implementation notes (from research)

**Phase 0 (must):**
- Script+agent-first or Script+user: **no** bootstrap `send_realtime_input`.
- Rely on `FirstSpeakerSection` + ScriptTiming SI only.
- Optional: gate mic until first Script inject **only** if freestyle still leaks (prefer SI-only first).

**Phase 1 scenario authoring template (dialogue):**

```jsonl
{"kind":"Persona","spec":{"name":"Mai","brief":"…","situation":"Signing up; unsure about monthly fee","goals":["Give name when asked","Ask what the monthly fee is","Decide to continue or decline"],"style":"polite, slightly hesitant","constraints":["Will not give a credit card number"],"traits":["quiet"]}}
{"kind":"Context","spec":{"notes":["Knows email is mai@example.com"]}}
{"kind":"PassCriteria","spec":{"… outcome: agent stated a fee or said fee unknown …"}}
# no Script — or hang_up-only as safety timeout
```

**Phase 2 SI for dialogue** (Google one-time vs loop):
- One-time: open / identify / ask fee.
- Loop: clarify plan, push back, negotiate — “OK to stay in this loop until outcome or hang.”
- Guardrails: constraints with if→then examples (already partially in `ConstraintsSection`).

**Phase 3 hybrid:** Script steps with metadata `role: fixture|forced_line`; freestyle unmuted except farewell while Script hang_up pending.

### J. Confidence

| Claim | Status |
|-------|--------|
| Bootstrap realtime text causes double speech | **verified** (code + prior run) |
| Industry splits persona vs task vs interaction fixtures | **verified** (Coval, Hamming, Future AGI) |
| Dialogue mode needs situation→outcome eval | **verified** (Future AGI + Coval metrics) |
| GCP mid-session `role=system` SI update works on our `google-genai` path | **unverified** — needs `.venv` proof before relying |
| People-pleaser risk without constraints/traits | **verified** (Coval eval guide) |

---

## If you want more detail
**Why Script-only feels “ít nói + ngắn”:** Interaction tests optimize for **reproducible audio events**, not rapport. A real human answers name, clarifies plan, pushes back on fee — that is **goal-seeking freestyle**, which Script hard-silence was designed to suppress.

**lk-sim unique value to keep:** LiveKit room + Gemini Live voice + Script fixtures for barge/noise + stereo player. Do **not** become a text-only chat sim; keep audio-native, but let Persona drive most words.

**Status:** Waiting for your OK — reply **go ahead** to implement (recommend Phase 0 first).
