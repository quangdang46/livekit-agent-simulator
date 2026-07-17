# WIP — roadmap toward replacing “open mic and talk to the agent”

**Goal:** `lk-sim` is the day-to-day black-box QA loop for LiveKit voice agents — real room (WebRTC or SIP), Gemini as the human, forensic report + web replay, CI-friendly gates, MCP for coding agents.

**Not the goal:** LiveKit in-process `AgentSession` unit tests; 50k concurrent load platforms; production observability SaaS (Hamming Observe / Coval online eval).

Update this file when gaps close or priorities change.

**Deep research:** [docs/caller-behavior-research.md](docs/caller-behavior-research.md)  
(Hamming persona / interruption / tests-as-code / workflow / metrics; Coval persona≠task + knobs; Cekura suite mix → packages C1–C8.)

---

## Research (2026-07-14)

### Sources

| Source | What it informed |
|---|---|
| [LiveKit Agents testing](https://docs.livekit.io/agents/start/testing/) + [test framework](https://docs.livekit.io/agents/start/testing/test-framework/) | In-process layer — **no real WebRTC room** |
| [LiveKit Agents evals](https://docs.livekit.io/reference/python/livekit/agents/evals/evaluation.html) | `JudgeGroup`, multi-judge |
| [LiveKit metrics](https://github.com/livekit/agents) (EOU / TTFT / TTFB) | White-box; we measure black-box TTFW / turn / recovery |
| [Telephony](https://docs.livekit.io/telephony/) + [DTMF](https://docs.livekit.io/telephony/features/dtmf/) + [AMD](https://docs.livekit.io/telephony/features/answering-machine-detection/) + [transfers](https://docs.livekit.io/telephony/features/transfers/) | Digits, machine detect, warm/cold transfer |
| Prebuilts: `GetDtmfTask`, `send_dtmf_events`, `WarmTransferTask`, `EndCallTool`, `AMD` | What agents ship — what black-box sim must exercise |
| Hamming / Coval / Cekura (see research doc) | Character = test artifact; barge taxonomy; layered asserts; persona≠task |
| Internal | `asserts.py`, `script/models.py`, `behavior_compile.py`, `sim_leg/`, `docs/telephony.md`, `docs/PROBLEM.md` |

### How testing splits (LiveKit stance)

| Layer | Owner | What it is |
|---|---|---|
| **In-process unit / session** | LiveKit Agents + pytest | Fake models, `result.expect…` — **no real room** |
| **Black-box room / audio E2E** | Hamming, Coval, Cekura, **lk-sim** | Real room + sim caller |
| **Load** | `lk perf`, Hamming | Many rooms |
| **Prod observe** | OTel, SaaS | Drift, alerts |

**lk-sim complements Agents pytest; it does not replace it.**

### Competitive snapshot

| Capability | lk-sim | Hamming / Coval / Cekura | LiveKit pytest |
|---|---|---|---|
| Real LiveKit room | ✅ | ✅ | ❌ |
| AI persona caller | ✅ Gemini Live | ✅ | ❌ |
| Barge / silence / noise | ✅ | ✅ | ❌ |
| **Typed interruption classes** | ❌ gap | ✅ | ❌ |
| Recovery + latency hard gates | ✅ | ✅ | partial |
| Forensic local + web + MCP | ✅ strong | cloud / rare MCP | partial |
| pass@k / fail→golden | ✅ | ✅ | manual |
| SIP modes | ✅ SimLeg | ✅ | ❌ |
| DTMF / IVR scripting | ❌ | ✅ | ❌ |
| AMD / voicemail sim | ❌ | ✅ | ❌ |
| Warm transfer observe | ❌ | partial | ❌ |
| Multi-judge / golden baseline | ⚠️ thin | ✅ | partial |
| Authoring quality gate | ⚠️ weak | ✅ rubric | n/a |
| Accent / load / prod SaaS | ❌ | ✅ | ❌ |

**Niche:** open-source, local-first, forensic-first, MCP-native black-box LiveKit.

---

## Already in good shape

| Area | Notes |
|---|---|
| Black-box dispatch | Opaque `Dispatch.metadata` |
| Gemini Live sim human | Persona / Context / Execute / Behavior |
| Transport modes | `webrtc_sim` · `inbound_sip` · `outbound_human_pickup` · `outbound_sim_callee` · `agent_dials` |
| SimLeg architecture | Strategy + factory; one observe/assert/judge pipeline |
| Caller character v1 | traits, constraints, `speech_conditions`, Behavior → Script |
| Scripted cues | triggers `agent_speaking` / silence / time; actions `speak` / `wait` / `hang_up` |
| Cue catalog + mixer | builtin + target cues; `noise_gain`; vocal barge WAV |
| Barge / silence / hangup | recovery asserts, `ended_by`, dead_call guard |
| Asserts | tools, transcript, recovery, latency, ended_by, goals_met, SIP |
| Metrics | p50/p95/p99, TTFW, recovery, barge rate |
| pass@k / suite / parallel | `--repeat`, `--pass-at-k`, `execute-all --parallel` |
| Fail → golden draft | `scenario-from-run` (smart extract: goals/constraints + Behavior stub from markers — P1.J done) |
| Forensics + web + MCP + portable install | events, WAV, report player, CLI↔ops parity |

P0 “talk like a person” ≈ **done v1**.  
P1 “daily regression loop” ≈ **done v1**.  
Telephony **modes** ≈ **done v1** (DID ops still open — P1.E).

---

## Open gaps (full backlog)

Ranked by ROI. Feature **and** logic gaps from research.

### P1 — Do next (caller realism + CI quality)

| ID | Gap | Type | Why | Candidate work | Research |
|---|---|---|---|---|---|
| **P1.A** | **DTMF / IVR scripting** | Feature + E2E logic | LiveKit `GetDtmfTask` / `send_dtmf_events`; Hamming/Coval IVR | Script action `dtmf` (`"1234#"`, `w`=pause); events `sim.dtmf` / `sip.dtmf.*`; Assert sequence; templates `ivr-pin-entry` | C2 / F1 / L3 |
| **P1.F** | **Typed interruption classes** | **Logic (core)** | Hamming: barge is not a bool | `class`: `correction` \| `backchannel` \| `noise` \| `dtmf` \| `silence` \| `escalate`; Behavior fields `barge_ins` / `backchannels` / `false_interrupts`; events + web chips by class | C1 / L1 |
| **P1.F1** | **Backchannel non-barge path** | Logic + feature | “uh-huh” must not cancel critical agent audio | `barge_in=false` + short PCM; assert agent continued (heuristic/judge); separate from recovery | F2 |
| **P1.F2** | **False-interrupt / noise click** | Logic + events | Hamming false_positive | Typed noise cue mid-agent; event `interruption.false_positive`; don’t count as recovery success | F3 |
| **P1.B** | **AMD / voicemail / machine personas** | Feature + state machine | LiveKit AMD; outbound leave-message | Presets: greeting WAV → beep → silence; optional no Gemini chat until timeout; assert leave-msg / hang / tools | C3 / F5 / L4 |
| **P1.B1** | **Silent mode (dead caller)** | Feature + logic | Coval Silent Mode | Full silence; disable barge+noise when silent; assert agent reprompt; interact with `caller_nudge` policy | F4 / L6 |
| **P1.G** | **Authoring quality gate** | Process + validate | Hamming persona rubric 0–14 | `validate` warnings: empty goals; barge without recovery assert; `interrupts` trait without Behavior/Script; risk tags; optional score | C4 / L2 |
| **P1.G1** | **Trait → Script enforcement** | Logic | Traits are soft; CI needs hard interaction | Soft default: `interrupts` → one barge if no Script; **warn** if trait implies stress but zero steps; never trust prompt alone for CI | L2 |
| **P1.H** | **Constraint-respect assert** | Assert + judge | Constraints only in prompt today | Outcome `constraint_respected` / must_not phrases + optional judge; people-pleaser counter | C8 / F9 / L8 |
| **P1.H1** | **People-pleaser counters** | Logic + authoring | Coval: LLM callers over-cooperate | Force refuse Script lines; wrong-date correction step; hang_up after threat path; don’t rely on traits alone | L8 |
| **P1.C** | **Multi-judge PassCriteria** | Feature | LiveKit JudgeGroup; Hamming layered eval | `judges[]` all / majority / weighted; backward compatible single list | C5 / F7 |
| **P1.D** | **Golden baseline compare** | Feature | CI before/after | `compare --baseline <run-id\|suite>` hard-fail latency/assert deltas | F8 |
| **P1.E** | **outbound_sim_callee reliability** | Ops | DID hairpin (`docs/PROBLEM.md`) | Preflight + DID/dispatch recipe; T6 sip-to-ai only if needed | P1.E |
| **P1.I** | **Tool required_order ledger** | Assert | Hamming workflow | Assert tools in order (not only min_count); fail on wrong sequence | Workflow |
| **P1.J** | **scenario-from-run extract quality** ✅ | Logic | Fail→golden flywheel | Done (#34): goals/constraints preferred over transcript; brief = mission statement; 1 Behavior barge/noise/backchannel stub from `sim.script.cue` markers; transcript sample → Context.notes; Script open when `first_speaker=user`; review checklist in draft header | C6 / L7 |
| **P1.K** | **Interruption rate timer** | ✅ done (#25) | Coval None/Low/Med/High (~never/90s/45s/30s) | `speech_conditions.interruption_rate` → parallel `InterruptRateRunner` (fires only while agent is active speaker; `interruption_*` overrides; disabled by silent_mode) | C7 / F10 |
| **P1.L** | **Event taxonomy polish** | Events + web | Hamming interruption lifecycle | `interruption.recovered`, class on cues, behavior_summary by class; web chips | Events |

### P2 — Production-adjacent (defer unless a target needs it)

| ID | Gap | Notes |
|---|---|---|
| **P2.A** | Warm transfer / SIP REFER **observe** | Events + asserts for `WarmTransferTask` / REFER; optional 2nd Gemini human (advanced) — L5 |
| **P2.A1** | `no_unplanned_handoff` assert | Hamming outcome type |
| **P2.B** | Accent / multi-voice matrix | Trait + locale only; no neural models |
| **P2.C** | Concurrent load rooms | Use `lk perf`; no Gemini N-way |
| **P2.D** | Prod import / shadow replay | SaaS territory |
| **P2.E** | Audio-native eval (WER, prosody) | Transcript + judge only today |
| **P2.F** | OTel export | Optional later |
| **P2.G** | Multi-party handoff | After P2.A |
| **P2.H** | Text-fast mode | Cheap loop before full voice |
| **P2.I** | Language / voice **scenario matrix** | Manual ⚠️ today — suite recipe, not one-off config |
| **P2.J** | Hold-music timeout hang — ✅ done (#29): `Execute.spec.hold_music_timeout_s` (5–300 s, Persona alias) → sim hang-up on agent dead air, reason `hold_music_timeout` | Coval advanced disconnect |
| **P2.K** | Owner / source_pattern / risk metadata | Hamming tests-as-code fields on Scenario (tags or optional fields) |

### P0 residual polish (optional)

- Broader EN/VI speech WAV pack (wait / sorry / backchannel variants)
- Document `noise_gain` / SNR as first-class speech_conditions (code already has `noise_gain`)
- Clearer `barge_in.auto_blip` vs vocal PCM in templates
- Suite mix **recipe** in docs (Cekura 60% standard / 20% challenging / 10% non-native / 10% edge)

---

## Gap index (feature vs logic)

Quick map of “what we’re missing” — full narrative in research doc + prior gap analysis.

### Features (product surface)

| # | Missing | Backlog ID |
|---|---|---|
| F1 | DTMF / IVR | P1.A |
| F2 | Backchannel non-barge | P1.F1 |
| F3 | False-interrupt typed | P1.F2 |
| F4 | Silent mode | P1.B1 |
| F5 | Voicemail / machine / IVR-only | P1.B |
| F6 | Warm transfer observe | P2.A |
| F7 | Multi-judge | P1.C |
| F8 | Golden baseline compare | P1.D |
| F9 | Constraint-respect assert | P1.H |
| F10 | Interruption rate timer | P1.K |

### Logic (engine / semantics)

| # | Missing | Backlog ID |
|---|---|---|
| L1 | Interruption taxonomy (classes + decisions) | P1.F |
| L2 | Trait → Script enforcement; constraints hard | P1.G1, P1.H |
| L3 | DTMF end-to-end path | P1.A |
| L4 | Machine-answer state machine | P1.B |
| L5 | Transfer room lifecycle observe | P2.A |
| L6 | Silent vs nudge / barge conflict rules | P1.B1 |
| L7 | scenario-from-run smart extract | P1.J |
| L8 | People-pleaser counters | P1.H1 |
| L9 | Tool order ledger | P1.I |
| L10 | Event taxonomy + summary by class | P1.L |

### Authoring / process

| # | Missing | Backlog ID |
|---|---|---|
| A1 | Validate quality rubric / warnings | P1.G |
| A2 | Risk tiers blocking / scheduled / exploratory | P2.K + P1.G |
| A3 | Suite mix recipe | P0 residual docs |
| A4 | Warn barge without recovery | P1.G |
| A5 | Template packs (standard / interrupt / silent / ivr / voicemail) | P1.A, P1.B, P1.F |

### Ops

| # | Missing | Backlog ID |
|---|---|---|
| O1 | sim-callee DID preflight + recipe | P1.E |

---

## Script / assert surface

**Script actions (`script/models.py`):**

```text
today:    speak | wait | hang_up
needed:   + dtmf
          + typed mid-call metadata (class on steps; backchannel / false_interrupt via Behavior)
```

**Assert outcomes:**

```text
today:    transcript_contains | llm_bool | recovery | latency | ended_by | goals_met
          + sip: participant_present | call_status_any | dial_answered
needed:   + dtmf sequence
          + constraint_respected (or must_not / policy)
          + tool required_order
          + (later) transfer lifecycle / no_unplanned_handoff
          + (later) backchannel_did_not_cancel heuristic
```

**Behavior / speech_conditions needed:**

```text
today:    barge_ins, user_silence, ambient; speech_conditions barge/noise/silence
needed:   + backchannels[], false_interrupts[], dtmf[]
          + interruption_rate | silent_mode compile rules
          + class on every mid-call step
```

---

## Developer manual work vs lk-sim

| Manual developer | lk-sim today | Gap ID |
|---|---|---|
| Open mic, greet agent | ✅ | — |
| Happy-path flow | ✅ | — |
| Barge-in, silence, noise | ✅ coarse | P1.F taxonomy |
| Backchannel without derailing agent | ❌ / weak | P1.F1 |
| Switch language / voice matrix | ⚠️ | P2.I |
| Check tool calls | ✅ | P1.I order |
| Check interruption recovery | ✅ | P1.F classes |
| Re-listen | ✅ | — |
| Fail CI on slow turns | ✅ | — |
| Stable pass under flake | ✅ | — |
| Promote bug to regression | ✅ draft quality | P1.J |
| Sim hard hangup | ✅ | — |
| Suite before ship | ✅ | P1.G quality |
| Inbound / outbound SIP | ✅ modes | P1.E DID |
| DTMF / PIN / IVR | ❌ | P1.A |
| Voicemail / machine answer | ❌ | P1.B |
| Silent / unresponsive caller | ⚠️ | P1.B1 |
| Constraint “won’t share card” hard | ⚠️ prompt only | P1.H |
| Warm transfer QA | ❌ | P2.A |
| Load 100 concurrent | ❌ (`lk perf`) | P2.C |
| Golden baseline compare | ⚠️ | P1.D |

---

## Suggested order

```text
Done v1
  · P0 behavior (persona, barge, silence, mixer, cues)
  · P1 regression (suite, exit policy, latency, pass@k, hang_up, goals_met)
  · SimLeg telephony modes + SIP asserts

Next (logic + telephony + CI quality)
  1. P1.F   Typed interruption classes (+ F1 backchannel, F2 false_interrupt)  ← logic foundation
  2. P1.A   DTMF script + events + asserts + IVR templates
  3. P1.G   Authoring validate (rubric warnings, trait↔Script, barge↔recovery)
  4. P1.H   Constraint-respect assert + people-pleaser counters (H1)
  5. P1.B   AMD / voicemail / machine (+ B1 silent mode)
  6. P1.C   Multi-judge PassCriteria
  7. P1.D   Golden baseline compare
  8. P1.E   outbound_sim_callee DID preflight + docs
  9. P1.I   Tool required_order
 10. P1.J   scenario-from-run extract quality
 11. P1.K   Interruption rate timer (Coval-style)
 12. P1.L   Event taxonomy + web chips by class
 13. P0 residual WAV / noise_gain docs / suite mix recipe
 14. P2.A   Warm-transfer observe when needed
 15. Later: P2.B–K, T6 sip-to-ai
```

**Note:** Earlier “DTMF first” still valid for telephony demos; **P1.F first** if the goal is correct Hamming-aligned caller semantics (otherwise backchannel pollutes recovery metrics).

Keep portable: no consumer keys in `src/`; extend via scenario / `.agent-sim/cues` / config / verify plugins (`AGENTS.md`, `docs/portability.md`).

---

## Explicit non-goals (for now)

- Replacing LiveKit **in-process** Agents pytest / FakeActions
- White-box EOU / TTFT / TTFB inside `AgentSession`
- Full Hamming/Coval production monitoring SaaS
- 50k concurrent simulation / Gemini N-way load
- Built-in neural accent models
- Domain hardcoding one monorepo’s business into core
- Owning telephony providers (trunks, numbers, billing, compliance)
- Observe-only PSTN without Gemini (**Gemini always replaces the human on the wire**)
- Auto-gen hundreds of scenarios from agent system prompt (needs white-box prompt)

---

## Status board

| Area | Status |
|---|---|
| Core sim + report + web replay | **Done** |
| P0 behavior + cue catalog | **Done (v1)** |
| Caller character v1 (constraints / Behavior / recovery) | **Done (v1)** — taxonomy still open |
| Suite + CI exit gate | **Done (v1)** |
| Latency metrics hard | **Done (v1)** |
| pass@k | **Done (v1)** |
| Fail → golden draft | **Done (v1)** — extract quality **open** (P1.J) |
| Hard hangup | **Done (v1)** |
| SimLeg modes + SIP asserts | **Done (v1)** |
| goals_met | **Done (v1)** |
| **Typed interruption classes** | **Next (P1.F)** |
| **DTMF / IVR** | **Next (P1.A)** |
| **Authoring validate / trait enforcement** | **Next (P1.G)** |
| **Constraint-respect + people-pleaser** | Planned (P1.H) |
| **AMD / voicemail / silent** | Planned (P1.B / B1) |
| Multi-judge PassCriteria | Planned (P1.C) |
| Golden baseline compare | Planned (P1.D) |
| outbound_sim_callee DID ops | Planned (P1.E) |
| Tool order / from-run extract / rate timer / events | Planned (P1.I–L) |
| Warm transfer observe | Deferred P2 (docs note in telephony.md; no core yet) |
| Load / accent / prod observe | Deferred P2 |

---

## Known problem — Gemini as SIP callee

Mode **`outbound_sim_callee`** needs a **sim DID** hairpinned into the sim-room. Real PSTN ≠ Gemini (split rooms).

| Mode | Use when |
|---|---|
| `outbound_human_pickup` | Manual: human answers; Gemini joins **same** agent-room |
| `outbound_sim_callee` | Automated: Gemini SIP callee via sim DID + dispatch |

Mitigations: DID + dispatch recipe + preflight → optional Twilio hairpin → optional T6 `references/sip-to-ai`.  
See `docs/PROBLEM.md`, `docs/telephony.md`. Work: **P1.E**.

---

## PR / merge process (mandatory — 2026-07-14)

**One gap / feature = one PR into `main`.** No multi-feature dumps.

### Merge-ready only when ALL hold

| Gate | Required |
|---|---|
| Unit tests | `uv run pytest -q` green (or scoped + full before merge) |
| Build | package importable; web build if `web/` touched |
| Real execute | `lk-sim execute <scenario> --root <any-target>` with **real** `reports/<run-id>/` matching expected gate — **except** pure tests/docs (no runtime surface) |
| Portable core | AGENTS.md: no hardcoding of worker/dashboard/product in `src/`; target is black-box smoke only |

### Forbidden
- Fitting core to voice-ai-worker or any one monorepo
- Merging DTMF/runtime without honest E2E (fake green)
- Multiple beads in one PR
- Direct multi-feature commits to main without PR review trail

### Draft PRs
OK when E2E cannot be honest yet; must state missing proof; do not merge until real report exists.

### Branch
`feat/<id>-slug` or `fix/<id>-slug` from latest `main`.

---

## Design locks (do not regress)

1. **Black-box agent** — never import or patch target application code.
2. **Gemini = simulated human** on every topology — no observe-only PSTN mode.
3. **Dialog vs interaction** — LLM says *what*; Script/Behavior says *when* (CI-critical must be Script/Assert, not traits alone).
4. **Mode only changes SimLeg** — one SimBrain + ObserveReport + VerifyJudge.
5. **Mode lives in scenario `Caller.mode`**, never in shared `config.yaml`.
6. **Opaque `Dispatch.metadata`** — core does not parse business keys.
7. **Provider-agnostic telephony** — trunk IDs / DIDs from config+scenario only.
8. **CLI ↔ MCP single ops path** — no forked run logic.
9. **Hard CI gate** on status/assert/script; judge soft unless `--strict-judge`.
10. **Persona quality rule** — if two agents pass on different workflows, persona/asserts are underspecified (Hamming).

---

## Package ID crosswalk (research doc)

| Research package | WIP IDs |
|---|---|
| C1 Typed interruptions | P1.F, P1.F1, P1.F2, P1.L |
| C2 DTMF | P1.A |
| C3 Machine / silent presets | P1.B, P1.B1 |
| C4 Authoring quality | P1.G, P1.G1 |
| C5 Multi-judge | P1.C |
| C6 scenario-from-run quality | P1.J |
| C7 Interruption rate | P1.K |
| C8 Constraint judge | P1.H, P1.H1 |
