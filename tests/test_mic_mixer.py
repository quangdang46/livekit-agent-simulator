import array
import struct

import pytest

from livekit_agent_simulator.audio.mic_mixer import (
    mix_pcm16_layers,
    scale_pcm16_samples,
    _pcm_to_samples,
)


def test_mix_pcm16_layers_sums_and_saturates() -> None:
    a = array.array("h", [1000, 2000, 30000])
    b = array.array("h", [500, -1000, 10000])
    out = mix_pcm16_layers(a, b)
    assert list(out) == [1500, 1000, 32767]  # last saturates


def test_mix_empty() -> None:
    assert list(mix_pcm16_layers()) == []
    assert list(mix_pcm16_layers(None, array.array("h"))) == []


def test_pcm_to_samples_roundtrip() -> None:
    raw = struct.pack("<3h", -100, 0, 200)
    samples = _pcm_to_samples(raw)
    assert list(samples) == [-100, 0, 200]


def test_scale_pcm16_samples() -> None:
    samples = array.array("h", [1000, -2000, 30000])
    out = scale_pcm16_samples(samples, 0.5)
    assert list(out) == [500, -1000, 15000]


def test_mixer_push_speech_applies_gain() -> None:
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:  # noqa: ANN001
            pass

    src = _FakeSrc()
    # preroll=0: unit tests exercise immediate frame mix, not jitter waterline
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)  # type: ignore[arg-type]
    n = mixer.frame_samples
    speech = array.array("h", [1000] * n)
    mixer.push_speech(speech.tobytes(), gain=0.5)
    mixer.end_speech_turn()
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 500


def test_mixer_clear_speech_drops_queue() -> None:
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:  # noqa: ANN001
            pass

    src = _FakeSrc()
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)  # type: ignore[arg-type]
    n = mixer.frame_samples
    mixer.push_speech(array.array("h", [1000] * n).tobytes())
    assert mixer.speech_queued_ms() > 0
    mixer.clear_speech()
    assert mixer.speech_queued_ms() == 0


@pytest.mark.asyncio
async def test_mixer_wait_speech_drain() -> None:
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:  # noqa: ANN001
            pass

    src = _FakeSrc()
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)  # type: ignore[arg-type]
    mixer.start()
    n = mixer.frame_samples
    mixer.push_speech(array.array("h", [1000] * n * 3).tobytes())
    mixer.end_speech_turn()
    await mixer.wait_speech_drain(timeout_s=2.0)
    assert mixer.speech_queued_ms() == 0
    await mixer.aclose()


def test_mixer_pop_frame_mixes_speech_and_noise() -> None:
    """Unit test frame mix without LiveKit AudioSource."""
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        def __init__(self) -> None:
            self.frames: list[bytes] = []

        async def capture_frame(self, frame) -> None:  # noqa: ANN001
            self.frames.append(bytes(frame.data))

    src = _FakeSrc()
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)  # type: ignore[arg-type]
    n = mixer.frame_samples

    # Speech: full scale tone-ish samples
    speech = array.array("h", [1000] * n)
    # Noise: overlaps fully
    noise = array.array("h", [500] * n)
    mixer.push_speech(speech.tobytes())
    mixer.end_speech_turn()
    mixer.push_noise(noise.tobytes())

    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert len(out) == n
    assert out[0] == 1500
    assert mixer.noise_remaining_ms() == 0
    assert mixer.speech_queued_ms() == 0


def test_mixer_noise_loop_requeues() -> None:
    """Looped ambient bed keeps producing samples after one template length."""
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:
            pass

    mixer = ParallelMicMixer(_FakeSrc(), sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)
    n = mixer.frame_samples
    # Template shorter than one frame so refill is forced every pop.
    noise = array.array("h", [100] * max(1, n // 2))
    mixer.push_noise(noise.tobytes(), loop=True)
    for _ in range(6):
        pcm = mixer._pop_frame()
        out = array.array("h")
        out.frombytes(pcm)
        assert out[0] == 100
    assert mixer.noise_remaining_ms() > 0
    mixer.clear_noise()
    assert mixer.noise_remaining_ms() == 0


def test_mixer_one_shot_noise_drains() -> None:
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:
            pass

    mixer = ParallelMicMixer(_FakeSrc(), sample_rate=24_000, frame_ms=10, speech_preroll_ms=0)
    n = mixer.frame_samples
    noise = array.array("h", [50] * n)
    mixer.push_noise(noise.tobytes(), loop=False)
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 50
    assert mixer.noise_remaining_ms() == 0


def _fake_src():
    class _FakeSrc:
        sample_rate = 24_000

        async def capture_frame(self, frame) -> None:  # noqa: ANN001
            pass

    return _FakeSrc()


def test_mixer_preroll_holds_until_waterline() -> None:
    """Active turn: do not emit speech until preroll buffered (no early crackle)."""
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    mixer = ParallelMicMixer(
        _fake_src(), sample_rate=24_000, frame_ms=10, speech_preroll_ms=50
    )  # type: ignore[arg-type]
    n = mixer.frame_samples
    # 20ms < 50ms waterline
    mixer.begin_speech_turn()
    mixer.push_speech(array.array("h", [1000] * n * 2).tobytes())
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 0
    assert mixer.speech_queued_ms() == 20  # still held

    # Cross waterline
    mixer.push_speech(array.array("h", [1000] * n * 4).tobytes())
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 1000


def test_mixer_mid_turn_underrun_does_not_pad_silence_into_speech() -> None:
    """Burst gap mid-turn: hold leftover samples; no zero punch into utterance."""
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    mixer = ParallelMicMixer(
        _fake_src(), sample_rate=24_000, frame_ms=10, speech_preroll_ms=20
    )  # type: ignore[arg-type]
    n = mixer.frame_samples
    mixer.begin_speech_turn()
    # Exactly waterline + one frame, then underrun
    mixer.push_speech(array.array("h", [2000] * n * 3).tobytes())
    assert mixer._pop_frame()  # start playing (preroll met)
    # Consume remaining speech until empty/partial would have padded
    while mixer.speech_queued_ms() >= 10:
        pcm = mixer._pop_frame()
        out = array.array("h")
        out.frombytes(pcm)
        assert out[0] == 2000

    # Underrun while turn still active — silence out, but late chunk must resume cleanly
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 0

    mixer.push_speech(array.array("h", [3000] * n * 3).tobytes())
    # Re-buffer to waterline (20ms) before emitting again
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    # 30ms queued after push; first pop after underrun may still be waterline wait
    # if playing reset — with 20ms preroll and 30ms queued, should play
    assert out[0] == 3000


def test_mixer_after_turn_complete_pads_silence() -> None:
    """After end_speech_turn, partial last frame may pad zeros (drain path)."""
    from livekit_agent_simulator.audio.mic_mixer import ParallelMicMixer

    mixer = ParallelMicMixer(
        _fake_src(), sample_rate=24_000, frame_ms=10, speech_preroll_ms=20
    )  # type: ignore[arg-type]
    n = mixer.frame_samples
    mixer.begin_speech_turn()
    mixer.push_speech(array.array("h", [1111] * (n // 2)).tobytes())
    mixer.end_speech_turn()
    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert out[0] == 1111
    assert out[-1] == 0  # silence pad after turn complete
    assert mixer.speech_queued_ms() == 0
