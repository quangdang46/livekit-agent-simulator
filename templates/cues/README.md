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
lk-sim cues --root /path/to/target
lk-sim cues --root /path/to/target --resolve builtin:noise.loud
```

## Built-in short IDs

| ID | File | Notes |
|----|------|--------|
| `noise.ambient` | `ambient_noise_bed.wav` | soft bed |
| `noise.loud` | `loud_noise_burst.wav` | burst |
| `noise.blip` / `noise.interrupt` | `loud_interrupt_blip.wav` | short cut-in blip |
| **`voice.barge_short`** / `voice.barge_wait` | `barge_wait_en.wav` | **speech** “Wait a second…” (EN) |
| **`voice.barge_sorry`** | `barge_sorry_en.wav` | speech “Sorry — one second…” |
| **`voice.backchannel`** / `voice.uhhuh` | `backchannel_uhhuh_en.wav` | speech “uh-huh” |
| `voice.barge_vi` | `barge_wait_vi.wav` | short VI barge |
| `backchannel` / `interrupt` / `ambiguous` | `*_ja.wav` | legacy JA samples |

Vocal `voice.*` assets are PCM speech mixed into the sim mic (`room_pcm`). Prefer them for audible barge-in; leave `with_blip: false` (default when asset is `voice.*`).

## Scenario examples

```json
{"id":"n1","trigger":"time","delay_ms":5000,"delivery":"room_pcm","asset":"builtin:noise.loud","say":"[noise]"}
```

```json
{"id":"b1","trigger":"agent_speaking","delay_ms":400,"delivery":"room_pcm","asset":"builtin:voice.barge_short","say":"[barge]","barge_in":true,"with_blip":false}
```

```json
{"id":"n2","trigger":"agent_speaking","delay_ms":400,"delivery":"room_pcm","asset":"cafe.wav","say":"[cafe]"}
```

Put `cafe.wav` in `.agent-sim/cues/` for that target repo only.

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
