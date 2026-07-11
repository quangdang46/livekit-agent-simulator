"""Local-first stereo conversation recorder (no LiveKit Egress).

Captures PCM already flowing through the sim bridge:
  - Left  channel = sim caller (Gemini TTS + room_pcm cues)
  - Right channel = agent (remote audio track subscribed by the sim)

Writes a single PCM16 stereo WAV under the run report directory.
"""

from __future__ import annotations

import array
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

# Match agent→Gemini bridge rate so agent frames need no resample.
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FILENAME = "conversation.wav"


def resample_pcm16_mono(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-resample mono PCM16 LE. No numpy/audioop dependency."""
    if not pcm:
        return b""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError(f"invalid sample rates: src={src_rate} dst={dst_rate}")
    if src_rate == dst_rate:
        return pcm
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = array.array("h")
    samples.frombytes(pcm)
    n_in = len(samples)
    if n_in == 0:
        return b""
    n_out = max(1, int(round(n_in * dst_rate / src_rate)))
    if n_in == 1:
        return array.array("h", [samples[0]] * n_out).tobytes()
    out = array.array("h", [0] * n_out)
    scale = (n_in - 1) / (n_out - 1) if n_out > 1 else 0.0
    for i in range(n_out):
        src_pos = i * scale
        j = int(src_pos)
        frac = src_pos - j
        if j >= n_in - 1:
            out[i] = samples[-1]
        else:
            out[i] = int(samples[j] * (1.0 - frac) + samples[j + 1] * frac)
    return out.tobytes()


@dataclass
class RecordResult:
    path: Path
    sample_rate: int
    duration_ms: int
    sim_samples: int
    agent_samples: int


class LocalConversationRecorder:
    """Thread-safe wall-clock stereo buffer: L=sim, R=agent."""

    def __init__(self, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._started_mono: float | None = None
        self._sim = array.array("h")
        self._agent = array.array("h")
        self._finalized = False

    @property
    def started(self) -> bool:
        return self._started_mono is not None

    @property
    def started_mono(self) -> float | None:
        """``time.monotonic()`` when recording t=0 was pinned (if started)."""
        return self._started_mono

    def mark_start(self) -> None:
        """Pin t=0 if not already started (call when sim mic publishes)."""
        with self._lock:
            if self._started_mono is None:
                self._started_mono = time.monotonic()

    def push_sim(self, pcm: bytes, sample_rate: int) -> None:
        self._push(self._sim, pcm, sample_rate)

    def push_agent(self, pcm: bytes, sample_rate: int) -> None:
        self._push(self._agent, pcm, sample_rate)

    def _push(self, channel: array.array, pcm: bytes, sample_rate: int) -> None:
        if not pcm or self._finalized:
            return
        converted = resample_pcm16_mono(pcm, sample_rate, self.sample_rate)
        if not converted:
            return
        samples = array.array("h")
        samples.frombytes(converted)
        now = time.monotonic()
        with self._lock:
            if self._started_mono is None:
                self._started_mono = now
            # Pad only the *gap* since the buffer's last sample (not wall-clock + chunk).
            # Continuous streams append with ~0 pad; sparse speech gets silence between turns.
            expected_s = self._started_mono + (len(channel) / self.sample_rate)
            gap_s = now - expected_s
            if gap_s > 0.02:
                pad = int(gap_s * self.sample_rate)
                if pad > 0:
                    channel.extend(array.array("h", [0] * pad))
            channel.extend(samples)

    def finalize(self, path: Path | str) -> RecordResult | None:
        """Write stereo WAV. Returns None if nothing was captured."""
        path = Path(path)
        with self._lock:
            if self._finalized:
                raise RuntimeError("LocalConversationRecorder already finalized")
            self._finalized = True
            sim = self._sim
            agent = self._agent
            if not sim and not agent:
                return None
            n = max(len(sim), len(agent))
            if n == 0:
                return None
            if len(sim) < n:
                sim = sim + array.array("h", [0] * (n - len(sim)))
            if len(agent) < n:
                agent = agent + array.array("h", [0] * (n - len(agent)))
            stereo = array.array("h", [0] * (n * 2))
            for i in range(n):
                stereo[i * 2] = sim[i]
                stereo[i * 2 + 1] = agent[i]
            duration_ms = int(n * 1000 / self.sample_rate)
            sim_samples = len(self._sim)
            agent_samples = len(self._agent)

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(stereo.tobytes())

        return RecordResult(
            path=path,
            sample_rate=self.sample_rate,
            duration_ms=duration_ms,
            sim_samples=sim_samples,
            agent_samples=agent_samples,
        )
