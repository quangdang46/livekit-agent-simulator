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
# Burst TTS (e.g. Gemini Live after barge) arrives in clumps. Playout needs a
# short waterline so the 10ms writer does not punch silence holes mid-utterance.
# Pattern: continuous PCM players use ~100–200ms preroll and only treat EOS as
# drained (see Persona worklet-playback-engine DEFAULT_PREBUFFER_MS=150; PJSIP
# jitter buffer minimum prefetch).
DEFAULT_SPEECH_PREROLL_MS = 150


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
    """Single-writer mic: mix speech queue + active noise tracks in real time.

    Speech jitter / preroll: while a Gemini (or other) TTS turn is active, the
    mixer does not emit speech until ``speech_preroll_ms`` is buffered, and on
    mid-turn underrun it re-enters that waterline instead of padding zeros into
    the utterance (which sounds like crackle). After ``end_speech_turn``
    (turn_complete), remaining samples drain with normal silence pad.
    """

    def __init__(
        self,
        source: rtc.AudioSource,
        *,
        sample_rate: int,
        recorder: LocalConversationRecorder | None = None,
        frame_ms: int = FRAME_MS,
        speech_preroll_ms: int = DEFAULT_SPEECH_PREROLL_MS,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if source.sample_rate != sample_rate:
            raise ValueError(
                f"mixer sample_rate {sample_rate} != AudioSource {source.sample_rate}"
            )
        if speech_preroll_ms < 0:
            raise ValueError("speech_preroll_ms must be >= 0")
        self.source = source
        self.sample_rate = sample_rate
        self.recorder = recorder
        self.frame_ms = frame_ms
        self.frame_samples = max(1, (sample_rate * frame_ms) // 1000)
        self.speech_preroll_ms = speech_preroll_ms
        self._preroll_samples = (sample_rate * speech_preroll_ms) // 1000

        self._lock = threading.Lock()
        self._speech = array.array("h")
        # Each noise track: (remaining samples, optional loop template)
        self._noise_tracks: list[tuple[array.array, array.array | None]] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._frames_written = 0
        self._speech_samples_in = 0
        self._noise_samples_in = 0
        # Turn gating for burst TTS (locked with _speech).
        self._speech_turn_active = False
        self._speech_playing = False

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

    def begin_speech_turn(self) -> None:
        """Mark a streaming TTS turn active (jitter hold until end_speech_turn)."""
        with self._lock:
            self._speech_turn_active = True
            self._speech_playing = False

    def end_speech_turn(self) -> None:
        """turn_complete — drain remaining speech; silence pad is OK after this."""
        with self._lock:
            self._speech_turn_active = False
            # Keep playing any leftover buffered samples without re-preroll.
            if self._speech:
                self._speech_playing = True
            else:
                self._speech_playing = False

    def push_speech(self, pcm: bytes, *, gain: float = 1.0) -> None:
        """Queue Gemini (or other) speech — plays mixed with any active noise."""
        samples = _pcm_to_samples(pcm)
        if not samples:
            return
        if gain != 1.0:
            samples = scale_pcm16_samples(samples, gain)
        with self._lock:
            # First chunk of a turn opens the turn if the caller forgot begin.
            if not self._speech_turn_active and not self._speech:
                self._speech_turn_active = True
                self._speech_playing = False
            self._speech.extend(samples)
            self._speech_samples_in += len(samples)

    def clear_speech(self) -> None:
        """Drop queued speech (e.g. mute hang-up token audio mid-turn)."""
        with self._lock:
            del self._speech[:]
            self._speech_turn_active = False
            self._speech_playing = False

    def push_noise(self, pcm: bytes, *, gain: float = 1.0, loop: bool = False) -> None:
        """Start a noise layer that plays in parallel with speech (does not block speech).

        ``loop=True`` re-queues the same PCM until the mixer stops (continuous ambient bed).
        """
        samples = _pcm_to_samples(pcm)
        if not samples:
            return
        if gain != 1.0:
            samples = scale_pcm16_samples(samples, gain)
        template = array.array("h", samples) if loop else None
        with self._lock:
            self._noise_tracks.append((samples, template))
            self._noise_samples_in += len(samples)

    def clear_noise(self) -> None:
        """Drop all active noise layers (including looping ambient beds)."""
        with self._lock:
            self._noise_tracks.clear()

    def noise_remaining_ms(self) -> int:
        with self._lock:
            if not self._noise_tracks:
                return 0
            # Looping beds report a positive remaining so waiters know noise is active.
            longest = 0
            for remaining, template in self._noise_tracks:
                if template is not None:
                    longest = max(longest, len(template))
                else:
                    longest = max(longest, len(remaining))
        return int(longest * 1000 / self.sample_rate)

    def speech_queued_ms(self) -> int:
        with self._lock:
            n = len(self._speech)
        return int(n * 1000 / self.sample_rate)

    async def wait_speech_drain(self, *, timeout_s: float | None = 3.0) -> None:
        """Wait until queued speech finishes (so hang-up does not clip the last phrase).

        Mid-turn underruns leave the queue empty while the turn is still active;
        drain waits for ``end_speech_turn`` as well as an empty queue.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while not self._stop.is_set():
            with self._lock:
                empty = len(self._speech) == 0
                active = self._speech_turn_active
            if empty and not active:
                return
            if deadline is not None and loop.time() >= deadline:
                return
            await asyncio.sleep(self.frame_ms / 1000.0)

    async def wait_noise_drain(self, *, timeout_s: float | None = None) -> None:
        """Optional: wait until active noise layers finish (speech may continue).

        Looping beds never drain on their own — only one-shot noise is waited on.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while not self._stop.is_set():
            with self._lock:
                pending = 0
                for remaining, template in self._noise_tracks:
                    if template is not None:
                        continue  # looping beds ignored for drain wait
                    pending += len(remaining)
            if pending == 0:
                return
            if deadline is not None and loop.time() >= deadline:
                return
            await asyncio.sleep(self.frame_ms / 1000.0)

    def _pop_speech_locked(self, n: int) -> array.array:
        """Take one frame of speech under lock (caller holds ``_lock``)."""
        silence = array.array("h", [0] * n)

        if self._speech_turn_active:
            if not self._speech_playing:
                # Preroll / re-buffer waterline — keep samples queued, emit silence.
                need = self._preroll_samples if self._preroll_samples > 0 else n
                if len(self._speech) >= need:
                    self._speech_playing = True
                else:
                    return silence
            if len(self._speech) >= n:
                speech = self._speech[:n]
                del self._speech[:n]
                return speech
            # Mid-turn underrun (empty or partial frame): do not punch zeros into
            # the utterance — hold remaining samples and re-enter waterline.
            self._speech_playing = False
            return silence

        # Turn complete: drain with silence pad (legacy continuous playout end).
        if len(self._speech) >= n:
            speech = self._speech[:n]
            del self._speech[:n]
            return speech
        if len(self._speech) > 0:
            speech = self._speech[:]
            self._speech = array.array("h")
            speech.extend([0] * (n - len(speech)))
            self._speech_playing = False
            return speech
        self._speech_playing = False
        return silence

    def _pop_frame(self) -> bytes:
        """Mix one frame of speech + noise under lock; return PCM16 bytes."""
        n = self.frame_samples
        with self._lock:
            speech = self._pop_speech_locked(n)

            noise_sum = array.array("h", [0] * n)
            still: list[tuple[array.array, array.array | None]] = []
            for remaining, template in self._noise_tracks:
                if template is not None:
                    # Continuous bed: refill from template when drained.
                    while len(remaining) < n:
                        remaining.extend(template)
                    chunk = remaining[:n]
                    del remaining[:n]
                    still.append((remaining, template))
                elif len(remaining) >= n:
                    chunk = remaining[:n]
                    del remaining[:n]
                    if remaining:
                        still.append((remaining, None))
                elif len(remaining) > 0:
                    chunk = remaining[:]
                    pad = n - len(chunk)
                    chunk.extend([0] * pad)
                    # one-shot drained after partial last frame
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
