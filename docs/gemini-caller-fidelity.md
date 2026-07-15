# Gemini caller fidelity — design

**Goal:** Gemini Live plays the *human caller* and follows scenario intent with high fidelity, without hardcoding any target agent.

## Layers (locked)

| Layer | Responsibility | Determinism |
|---|---|---|
| **Dialog policy** | *What* the caller says (goals, style, refuse language) | Stochastic LLM |
| **Interaction policy** | *When* to barge / silence / hang / DTMF | Deterministic Script/Behavior |
| **Proof** | CI hard/soft gates | Assert + optional judge |

Industry (Hamming, Coval) and Google Live SI best practices: **persona → ordered conversational rules → guardrails**, with interaction timing **not** left to free-form traits alone.

## Design pattern

```text
CallerPolicy (Protocol)
  ├─ build_system_instruction(ctx) -> str     # Strategy: prompt composition
  └─ midcall_cues(ctx) -> list[MidcallCue]    # Strategy: bootstrap (+ reserved reground)

DefaultCallerPolicy
  sections via PromptSection builders (Composite):
    RoleSection | GoalsSection | StyleTraitsSection | ConstraintsSection
    | SpeechConditionsSection | ContextSection | ScriptTimingSection
    | FirstSpeakerSection | GuardrailsSection

GeminiCallerBridge
  - owns Live session I/O only
  - receives instruction string + optional mid-call text injects
  - emits bootstrap when first_speaker=user at connect
  - inject_reground() exists but is **not auto-triggered** yet (reserved API)
  - does not know product/business keys
```

**Open/Closed:** new prompt sections or policies register without editing Live I/O.

## Controls that beat people-pleasing

1. Short fixed Script `say` for critical refuses (not traits alone)
2. Hard asserts (`constraint_respected`, `goals_met`, `ended_by`, recovery)
3. System instruction order + hard constraints (if→then) in the SI
4. Bootstrap text for `first_speaker=user`; agent-first uses `caller_nudge` after greeting
5. Mid-call re-ground inject — **reserved** (`inject_reground`); not a live control loop yet
6. `pass@k` for stochastic soft goals

## Non-goals

- White-box agent prompt reading
- Product-specific personas in core
- Replacing Script with prompt-only barge timing
