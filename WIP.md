# WIP — gaps toward replacing a developer talking to the agent

Goal: **`lk-sim` replaces manual “open mic and chat with the voice agent”** for day-to-day QA — full-stack LiveKit room, black-box agent, forensic report + web replay.

Not the goal (different products): LiveKit **in-process** text unit tests inside agent code; 50k concurrent load platforms; full production observability SaaS.

---

## Research note (2026-07-11)

Sources: [LiveKit Agents testing docs](https://docs.livekit.io/agents/start/testing/), LiveKit Series C / agents framework, Hamming 5-pillar LiveKit guide, Coval/Hamming/Cekura/Vapi simulation landscape.

### How the industry splits testing

| Layer | Who owns it | What it is |
|---|---|---|
| **In-process unit / session** | LiveKit Agents `AgentSession` + pytest/Vitest | Fake STT/LLM/TTS, `result.expect…`, LLM judge, metrics (EOU, TTFT, TTFB) — **no real WebRTC room** |
| **Black-box room / audio E2E** | Hamming, Coval, Cekura, Bluejay, Roark, **lk-sim** | Real (or near-real) room + audio pipeline + sim caller |
| **Load** | `lk perf agent-load-test`, Hamming concurrent | Many rooms / SIP load |
| **Prod observe** | OTel, Hamming/Cekura online eval | Drift, P90 latency, alerts |

LiveKit official stance: native tests for **agent logic**; for **full audio pipeline** they point to third-party tools (Hamming, Coval, Cekura, Bluejay). lk-sim sits in that E2E slot — **complement**, not replace, Agents pytest.

### Metrics industry cares about (that we partially have)

| Metric | Industry target (Hamming-ish) | lk-sim today |
|---|---|---|
| Turn-taking / TTFW | P90 e2e &lt; ~3.5s; first word often &lt;2.5s | `turn_taking_ms` in events — **not aggregated to suite/CI** |
| Barge-in recovery | Agent stops / re-engages within ~1 turn | Script + `interruption` + recovery asserts — **no timing gate** |
| Task completion | &gt;90% | Assert + PassCriteria judge |
| Flake / pass@k | Statistical eval | Single-shot runs |
| Fail → golden | Prod/sim failure → permanent test | Manual scenario write |
| Suite CI gate | Non-zero exit, matrix | `execute-all` thin; judge can flake exit |

### Audio realism competitors sell

Accent / noise / mid-sentence barge-in **timing** / voice characters — we have traits, mixer, cues catalog, barge blip+PCM; still weak on **speech WAV library (vi)**, accent matrix, barge **latency asserts**.

---

## Already in good shape

| Capability | Notes |
|---|---|
| Black-box room + dispatch | Opaque metadata; any LiveKit agent |
| Gemini Live sim caller | Persona / Context / Execute |
| Scripted cues | `agent_speaking`, `gemini_text` / `room_pcm` |
| Cue catalog multi-repo | `builtin:…`, `.agent-sim/cues/`, `cues.aliases` / `dirs` |
| Parallel speech+noise | `ParallelMicMixer` |
| Barge-in (harder) | blip PCM + text, `interruption` by sim, recovery asserts |
| Silence / user hold | `silence_after_cue_ms`, dead_call guard while scripted silence |
| Forensic log | `events.jsonl`, timeline, summary, SQLite |
| Optional LLM judge | `PassCriteria` |
| Local stereo audio | `conversation.wav` (L=sim, R=agent) |
| Web replay + markers | barge / silence / recovery highlight |
| Batch + compare | `execute-all`, `compare` (thin) |
| Scaffold / guide | `scenario-init`, `guide`, `install.sh`, `lk-sim cues` |
| CLI ↔ MCP parity | Single `ops` surface |

P0 “talk like a person on one call” ≈ **done for v1**.

---

## P0 — “Talk more like a real person” (closed for v1)

| Gap | Status | Notes |
|---|---|---|
| Diverse personas | **Done (v1)** | `Persona.traits[]` |
| Parallel speech+noise | **Done** | Mixer |
| Interruption / barge-in | **Done (v1)** | Still polish: vocal WAV library, optional skip auto-blip |
| Silence / user gone | **Done** | 20s hold + dead_call guard |
| Outcome / tool asserts | **Done** | Assert tools / transcript / outcomes |
| Multi-repo cues | **Done** | builtin + target override |

**P0 residual polish (optional, not blockers):**

- Vietnamese **speech** WAVs for barge (not only synthetic rè)
- Optional `barge_in.auto_blip: false` when using vocal PCM
- Stricter barge **timing** assert (cut within N ms of agent active)

---

## P1 — Daily QA / regression loop ← **do next**

Aligned with LiveKit/Hamming “regression + offline eval” without becoming SaaS.

| Priority | Gap | Why (from research) | Candidate work |
|---|---|---|---|
| **P1.1** | **Suite report + CI gate** | Hamming: automated regression is table stakes | **Done (v1)** — `execute-all` → `suite` matrix + `suite-*.json/md` |
| **P1.5** | **Exit-code policy** | CI needs hard gates | **Done (v1)** — hard: status/assert/script; judge soft unless `--strict-judge` |
| **P1.2** | **Flake control (pass@k)** | LLMs stochastic (LiveKit blog) | `execute --repeat N --pass-at-k`; store per-iter summaries |
| **P1.3** | **Voice metrics aggregates** | TTFW / turn-taking / barge rate are what devs “feel” | Derive from `events.jsonl` → `summary.metrics` + web chips |
| **P1.4** | **Promote failure → golden** | Hamming/Coval: prod/sim fail → permanent case | `lk-sim scenario-from-run <run-id>` draft JSONL from transcript + script markers |
| **P1.6** | **Text-fast mode** | Vapi-style cheap loop before full voice | Optional text path (later if suite cost hurts) |

---

## P2 — Scale / production-adjacent (defer)

| Gap | Notes |
|---|---|
| Concurrent / load rooms | `lk perf` / Hamming territory |
| SIP / telephony | Real numbers; Cekura telephony path |
| Prod import / shadow replay | Online eval SaaS |
| Auto-gen scenarios from prompt | Nice-to-have |
| Multi-party handoff | LiveKit multi-agent workflows |
| OTel export from lk-sim | Optional bridge to agent observability |

---

## Developer manual work vs lk-sim

| Manual developer | lk-sim today |
|---|---|
| Open mic, greet agent | ✅ |
| Happy-path flow | ✅ |
| Barge-in, silence, noise | ✅ (PCM cues; vocal custom possible) |
| Switch language / voice | ⚠️ config — no scenario matrix |
| Check tool calls | ✅ Assert.tools |
| Re-listen | ✅ wav + web |
| Compare before/after | ⚠️ compare — no golden baseline |
| Run 20 cases before ship | ✅ execute-all suite matrix + hard gate (judge soft) |
| Call real SIP | ❌ |
| Load 100 concurrent | ❌ |

---

## Suggested order (post-research)

1. ~~Behavioral richness (P0)~~ **Done v1**  
2. ~~Hard asserts~~ **Done**  
3. ~~**P1.1 + P1.5** — suite matrix + CI exit policy~~ **Done v1**  
4. **P1.3** — metrics on summary/web (TTFW, barge, turn-taking p50/p95)  
5. **P1.2** — pass@k for flaky judge scenarios  
6. **P1.4** — scenario-from-run  
7. Polish: VI speech cue pack; optional text-fast  
8. Later: SIP / load / OTel  

Keep portable: no consumer keys in `src/`; extend via scenario / `.agent-sim/cues` / config / verify plugins (`AGENTS.md`).

---

## Explicit non-goals (for now)

- Replacing LiveKit **in-process** Agents pytest / FakeActions session tests  
- Full Hamming/Coval-class production monitoring SaaS  
- 50k concurrent simulation  
- Domain hardcoding one monorepo’s business into core  

---

## Status

| Area | Status |
|---|---|
| Core sim + report + web replay | Done |
| P0 behavior + structured pass + cue catalog | **Done (v1)** |
| P1.1 suite + P1.5 exit gate | **Done (v1)** |
| P1 metrics / flake / promote-run | **Open — next** |
| P2 load / SIP / prod import | Deferred |

Update this file when gaps close or priorities change.
