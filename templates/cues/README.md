# Script audio cues (room_pcm)

**24 kHz, mono, 16-bit PCM WAV** files mixed into the sim caller mic (`delivery: room_pcm`).
The agent STT hears this audio (parallel with Gemini speech via `ParallelMicMixer`).

## Built-in vs custom (multi-repo)

| Layer | Where | How to reference |
|-------|--------|------------------|
| **Built-in** | package `templates/cues/` | `builtin:noise.loud`, `@noise.ambient`, or filename |
| **Target override** | `<repo>/.agent-sim/cues/` | same filename **wins** over package |
| **Alias** | `config.yaml` → `cues.aliases` | short name, e.g. `office` |
| **Extra dirs** | `cues.dirs` | more search paths under project root |
| **Next to scenario** | same folder as `.jsonl` | relative path |

```bash
lks cues --root /path/to/target
lks cues --root /path/to/target --resolve builtin:voice.correction
```

Regenerate EN SAPI vocals (Windows):

```bash
.venv\Scripts\python.exe scripts/generate_voice_interrupt_cues.py
```

## Built-in short IDs

### Noise / false interrupt

| ID | File | Notes |
|----|------|--------|
| `noise.ambient` | `ambient_noise_bed.wav` | soft bed |
| `noise.loud` | `loud_noise_burst.wav` | burst |
| `noise.blip` / `noise.interrupt` | `loud_interrupt_blip.wav` | short cut-in blip |

### Voice interrupt (Hamming-aligned)

Canonical descriptions also live in code (`BUILTIN_CUES` → `lks cues`).

| ID | File | Class | Notes |
|----|------|-------|--------|
| **`voice.correction`** / `voice.barge_correction` | `barge_correction_en.wav` | correction | “No wait - I meant next Friday.” |
| **`voice.escalate`** / `voice.human` | `barge_escalate_en.wav` | escalate | “Stop. I need to speak with a human…” |
| **`voice.soft`** / `voice.barge_soft` | `barge_soft_en.wav` | correction | soft “Um, hang on…” |
| **`voice.barge_short`** / `voice.barge_wait` / `voice.interrupt` | `barge_wait_en.wav` | correction | “Wait a second…” |
| **`voice.barge_sorry`** | `barge_sorry_en.wav` | correction | “Sorry — one second…” |
| **`voice.backchannel`** / `voice.uhhuh` | `backchannel_uhhuh_en.wav` | backchannel | “uh-huh” ×5 (~4s) |
| **`voice.backchannel_yeah`** / `voice.yeah` | `backchannel_yeah_en.wav` | backchannel | “Yeah. Okay. Mhm…” |
| **`voice.barge_vi`** | `barge_wait_vi.wav` | correction | short VI barge |
| **`voice.barge_long_vi`** | `barge_long_vi.wav` | correction | stacked VI (longer VAD) |
| **`voice.backchannel_vi`** / `voice.uhhuh_vi` | `backchannel_vi.wav` | backchannel | stacked VI sustain |
| `backchannel` / `interrupt` / `ambiguous` | `*_ja.wav` | legacy JA samples |

Product/locale dialogue (fee questions, goodbyes, brand lines) belongs in **Persona goals** or target `.agent-sim/cues/` — not package builtins.

Vocal `voice.*` assets are PCM speech mixed into the sim mic (`room_pcm`). Prefer them for audible barge-in; leave `with_blip: false` (default when asset is `voice.*`).

**Per-step volume:** set `"gain": 0.0–1.0` on any Script step (alias `"volume"`). Applies to `gemini_text` TTS and `room_pcm` cues. Persona `speech_conditions` supports `barge_gain` and `noise_gain` for auto-compiled steps.

**Continuous ambient bed:** set `"loop": true` on a `room_pcm` noise step (or `Persona.speech_conditions.noise_when: "background"` / `Behavior.ambient.loop: true`). The mixer re-queues the WAV under speech until hang-up. Prefer short seamless noise beds; longer custom WAVs can live in `.agent-sim/cues/`. Not for `voice.*` speech assets.

## Scenario examples

```json
{"id":"n1","trigger":"time","delay_ms":5000,"delivery":"room_pcm","asset":"builtin:noise.loud","say":"[noise]"}
{"id":"bed","trigger":"time","delay_ms":1500,"delivery":"room_pcm","asset":"builtin:noise.ambient","say":"[ambient]","loop":true,"gain":0.3}
```

```json
{"id":"b1","trigger":"agent_speaking","delay_ms":400,"delivery":"room_pcm","asset":"builtin:voice.correction","say":"[correction]","barge_in":true,"class":"correction","with_blip":false}
```

```json
{"id":"e1","trigger":"agent_speaking","delay_ms":800,"delivery":"room_pcm","asset":"builtin:voice.escalate","say":"[escalate]","barge_in":true,"class":"escalate","with_blip":false}
```

```json
{"id":"bc","trigger":"agent_speaking","delay_ms":1000,"delivery":"room_pcm","asset":"builtin:voice.backchannel_yeah","say":"[bc]","barge_in":false,"class":"backchannel","with_blip":false}
```

```json
{"id":"soft-barge","trigger":"agent_speaking","delay_ms":900,"say":"Wait — one second","barge_in":true,"gain":0.45,"with_blip":false}
```

Put project-specific WAVs under `.agent-sim/cues/` for that target repo only.

```yaml
# .agent-sim/config.yaml
cues:
  aliases:
    street: street_vn.wav
  dirs:
    - media/noise
```

```json
{"asset":"street","delivery":"room_pcm","say":"[street]","trigger":"time","delay_ms":3000,"id":"s1"}
```
