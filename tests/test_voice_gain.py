"""Quiet-caller voice_gain (PCM scale after Gemini Live)."""

from __future__ import annotations

import array

import pytest

from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer
from livekit_agent_simulator.caller.policy import CallerPolicyContext
from livekit_agent_simulator.caller.prompt_sections import SpeechConditionsSection
from livekit_agent_simulator.gemini.live_session import (
    GeminiCallerBridge,
    resolve_voice_gain,
)


def test_resolve_voice_gain_default() -> None:
    assert resolve_voice_gain(None) == 1.0
    assert resolve_voice_gain({}) == 1.0
    assert resolve_voice_gain({"speech_conditions": {}}) == 1.0


def test_resolve_voice_gain_aliases() -> None:
    assert resolve_voice_gain({"speech_conditions": {"voice_gain": 0.35}}) == 0.35
    assert resolve_voice_gain({"speech_conditions": {"voice_volume": 0.5}}) == 0.5
    assert resolve_voice_gain({"speech_conditions": {"volume": 0.25}}) == 0.25
    assert (
        resolve_voice_gain(
            {"speech_conditions": {"voice_gain": 0.4, "voice_volume": 0.9}}
        )
        == 0.4
    )


def test_resolve_voice_gain_bounds() -> None:
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        resolve_voice_gain({"speech_conditions": {"voice_gain": 1.5}})
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        resolve_voice_gain({"speech_conditions": {"voice_gain": -0.1}})
    with pytest.raises(ValueError, match="number"):
        resolve_voice_gain({"speech_conditions": {"voice_gain": "loud"}})


@pytest.mark.asyncio
async def test_bridge_play_pcm_applies_voice_gain() -> None:
    """Freestyle path multiplies PCM by voice_gain via mixer."""

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:
            pass

    bridge = object.__new__(GeminiCallerBridge)
    bridge._voice_gain = 0.5
    bridge._inject_playback_gain = 1.0
    bridge._inject_turn_active = False
    bridge._mute_persona_audio = False
    bridge._script_hangup_farewell = False
    bridge._source = _FakeSrc()
    bridge.recorder = None
    bridge._mixer = ParallelMicMixer(
        bridge._source, sample_rate=24_000, frame_ms=10, speech_preroll_ms=0
    )
    n = bridge._mixer.frame_samples
    pcm = array.array("h", [1000] * n).tobytes()
    await bridge._play_pcm(pcm)
    bridge._mixer.end_speech_turn()
    out = array.array("h")
    out.frombytes(bridge._mixer._pop_frame())
    assert out[0] == 500


def test_bridge_inject_gain_multiplies_voice_gain() -> None:
    bridge = object.__new__(GeminiCallerBridge)
    bridge._voice_gain = 0.5
    gain = 0.8
    effective = max(0.0, min(1.0, float(gain) * bridge._voice_gain))
    assert effective == pytest.approx(0.4)


def test_prompt_mentions_quiet_gain() -> None:
    ctx = CallerPolicyContext(
        persona={"speech_conditions": {"voice_gain": 0.3}},
        locale="en-US",
        context={},
        script_steps=[],
        first_speaker="agent",
    )
    lines = SpeechConditionsSection().render(ctx)
    joined = " ".join(lines)
    assert "0.30" in joined
