"""Parallel sim-mic mixer — speech + noise layers into one LiveKit AudioSource.

LiveKit ``AudioSource.capture_frame`` must be called from a single writer.
Real callers, however, speak *while* ambient noise / blips play. This mixer:

- accepts speech (Gemini TTS) and noise (room_pcm cues) concurrently
- mixes PCM16 mono samples each frame
- writes once per ~10ms to the AudioSource

Without this, locking around capture forces sequential audio (unrealistic).
"""

from __future__ import annotations

import array
import asyncio
import threading
from typing import TYPE_CHECKING

from livekit import rtc

if TYPE_CHECKING:
    from .local_recorder import LocalConversationRecorder

FRAME_MS = 10


def _pcm_to_samples(pcm: bytes) -> array.array:
    if not pcm:
        return array.array("h")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    a = array.array("h")
    a.frombytes(pcm)
    return a


def scale_pcm16_samples(samples: array.array, gain: float) -> array.array:
    """Scale PCM16 mono samples by linear gain (0.0–1.0 typical), with saturation."""
    if gain == 1.0 or not samples:
        return samples
    out = array.array("h", [0] * len(samples))
    for i, s in enumerate(samples):
        v = int(round(int(s) * gain))
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


def scale_pcm16_bytes(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0 or not pcm:
        return pcm
    return scale_pcm16_samples(_pcm_to_samples(pcm), gain).tobytes()


def mix_pcm16_layers(*layers: array.array | list[int] | None) -> array.array:
    """Sum aligned PCM16 samples with saturate; length = max(layer lengths)."""
    active = [array.array("h", layer) if not isinstance(layer, array.array) else layer
              for layer in layers if layer]
    if not active:
        return array.array("h")
    n = max(len(a) for a in active)
    out = array.array("h", [0] * n)
    for layer in active:
        for i, s in enumerate(layer):
            v = out[i] + int(s)
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            out[i] = v
    return out


class ParallelMicMixer:
    """Single-writer mic: mix speech queue + active noise tracks in real time."""

    def __init__(
        self,
        source: rtc.AudioSource,
        *,
        sample_rate: int,
        recorder: LocalConversationRecorder | None = None,
        frame_ms: int = FRAME_MS,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if source.sample_rate != sample_rate:
            raise ValueError(
                f"mixer sample_rate {sample_rate} != AudioSource {source.sample_rate}"
            )
        self.source = source
        self.sample_rate = sample_rate
        self.recorder = recorder
        self.frame_ms = frame_ms
        self.frame_samples = max(1, (sample_rate * frame_ms) // 1000)

        self._lock = threading.Lock()
        self._speech = array.array("h")
        self._noise_tracks: list[array.array] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._frames_written = 0
        self._speech_samples_in = 0
        self._noise_samples_in = 0

    @property
    def frames_written(self) -> int:
        return self._frames_written

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="mic-mixer")
        return self._task

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        self.stop()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def push_speech(self, pcm: bytes, *, gain: float = 1.0) -> None:
        """Queue Gemini (or other) speech — plays mixed with any active noise."""
        samples = _pcm_to_samples(pcm)
        if not samples:
            return
        if gain != 1.0:
            samples = scale_pcm16_samples(samples, gain)
        with self._lock:
            self._speech.extend(samples)
            self._speech_samples_in += len(samples)

    def clear_speech(self) -> None:
        """Drop queued speech (e.g. mute hang-up token audio mid-turn)."""
        with self._lock:
            del self._speech[:]

    def push_noise(self, pcm: bytes, *, gain: float = 1.0) -> None:
        """Start a noise layer that plays in parallel with speech (does not block speech)."""
        samples = _pcm_to_samples(pcm)
        if not samples:
            return
        if gain != 1.0:
            samples = scale_pcm16_samples(samples, gain)
        with self._lock:
            self._noise_tracks.append(samples)
            self._noise_samples_in += len(samples)

    def noise_remaining_ms(self) -> int:
        with self._lock:
            if not self._noise_tracks:
                return 0
            longest = max(len(t) for t in self._noise_tracks)
        return int(longest * 1000 / self.sample_rate)

    def speech_queued_ms(self) -> int:
        with self._lock:
            n = len(self._speech)
        return int(n * 1000 / self.sample_rate)

    async def wait_speech_drain(self, *, timeout_s: float | None = 3.0) -> None:
        """Wait until queued speech finishes (so hang-up does not clip the last phrase)."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while not self._stop.is_set():
            if self.speech_queued_ms() == 0:
                return
            if deadline is not None and loop.time() >= deadline:
                return
            await asyncio.sleep(self.frame_ms / 1000.0)

    async def wait_noise_drain(self, *, timeout_s: float | None = None) -> None:
        """Optional: wait until active noise layers finish (speech may continue)."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while not self._stop.is_set():
            with self._lock:
                pending = sum(len(t) for t in self._noise_tracks)
            if pending == 0:
                return
            if deadline is not None and loop.time() >= deadline:
                return
            await asyncio.sleep(self.frame_ms / 1000.0)

    def _pop_frame(self) -> bytes:
        """Mix one frame of speech + noise under lock; return PCM16 bytes."""
        n = self.frame_samples
        with self._lock:
            # Speech: take up to n samples (pad silence)
            if len(self._speech) >= n:
                speech = self._speech[:n]
                del self._speech[:n]
            elif len(self._speech) > 0:
                speech = self._speech[:]
                self._speech = array.array("h")
                pad = n - len(speech)
                speech.extend([0] * pad)
            else:
                speech = array.array("h", [0] * n)

            noise_sum = array.array("h", [0] * n)
            still: list[array.array] = []
            for track in self._noise_tracks:
                if len(track) >= n:
                    chunk = track[:n]
                    del track[:n]
                    still.append(track)
                elif len(track) > 0:
                    chunk = track[:]
                    # track exhausted this frame
                    pad = n - len(chunk)
                    chunk.extend([0] * pad)
                else:
                    continue
                for i in range(n):
                    v = noise_sum[i] + int(chunk[i])
                    if v > 32767:
                        v = 32767
                    elif v < -32768:
                        v = -32768
                    noise_sum[i] = v
            self._noise_tracks = still

            mixed = array.array("h", [0] * n)
            for i in range(n):
                v = int(speech[i]) + int(noise_sum[i])
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                mixed[i] = v

        return mixed.tobytes()

    async def _run(self) -> None:
        period = self.frame_ms / 1000.0
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        try:
            while not self._stop.is_set():
                pcm = self._pop_frame()
                # Always write (incl. silence) so playout clock stays steady while noise plays.
                frame = rtc.AudioFrame(
                    data=pcm,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    samples_per_channel=self.frame_samples,
                )
                await self.source.capture_frame(frame)
                if self.recorder is not None:
                    # Record what we actually sent (mixed), not raw layers alone.
                    self.recorder.push_sim(pcm, self.sample_rate)
                self._frames_written += 1

                next_t += period
                delay = next_t - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Fell behind — resync without busy spin
                    next_t = loop.time()
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
