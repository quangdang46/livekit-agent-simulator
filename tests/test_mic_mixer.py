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
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10)  # type: ignore[arg-type]
    n = mixer.frame_samples
    speech = array.array("h", [1000] * n)
    mixer.push_speech(speech.tobytes(), gain=0.5)
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
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10)  # type: ignore[arg-type]
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
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10)  # type: ignore[arg-type]
    mixer.start()
    n = mixer.frame_samples
    mixer.push_speech(array.array("h", [1000] * n * 3).tobytes())
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
    mixer = ParallelMicMixer(src, sample_rate=24_000, frame_ms=10)  # type: ignore[arg-type]
    n = mixer.frame_samples

    # Speech: full scale tone-ish samples
    speech = array.array("h", [1000] * n)
    # Noise: overlaps fully
    noise = array.array("h", [500] * n)
    mixer.push_speech(speech.tobytes())
    mixer.push_noise(noise.tobytes())

    pcm = mixer._pop_frame()
    out = array.array("h")
    out.frombytes(pcm)
    assert len(out) == n
    assert out[0] == 1500
    assert mixer.noise_remaining_ms() == 0
    assert mixer.speech_queued_ms() == 0
