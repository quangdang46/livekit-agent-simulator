# Script audio cues

Short **24 kHz, mono, 16-bit PCM WAV** files played directly into the sim caller mic
(`room_pcm` delivery). The pipeline agent STT hears this audio — not Gemini text.

Replace placeholders with real vocal recordings for adaptive backchannel tests.

| File | Use |
|------|-----|
| `backchannel_ja.wav` | Scenario A — brief backchannel |
| `real_interrupt_ja.wav` | Scenario B — longer interrupt phrase |
| `ambiguous_ja.wav` | Scenario C — very short ambiguous sound |

Scenario JSONL example:

```json
{
  "id": "backchannel",
  "trigger": "agent_speaking",
  "delay_ms": 900,
  "say": "うん",
  "delivery": "room_pcm",
  "asset": "backchannel_ja.wav"
}
```

`asset` resolves: scenario directory → `templates/cues/` in this package.
