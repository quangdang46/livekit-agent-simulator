"""Gemini Live simulated caller bridged into the LiveKit room.

Wire rules (verified for gemini-3.1-flash-live-preview, native audio):
    - response_modalities MUST be [AUDIO]; requesting TEXT closes the socket with 1011.
    - Input audio: raw PCM16 mono @16000 Hz via send_realtime_input(audio=Blob(...,
      mime_type="audio/pcm;rate=16000")).
    - Output audio: PCM16 mono @24000 Hz in server_content.model_turn parts inline_data.
    - Caller/agent text comes from input_audio_transcription / output_audio_transcription.
    - server_content.interrupted signals barge-in (agent audio interrupted the sim, or vice versa).

LiveKit side:
    - Agent audio in: rtc.AudioStream(track, sample_rate=16000) — SDK resamples 48k→16k.
    - Sim audio out: rtc.AudioSource(24000, 1) — no manual resampling; WebRTC handles playback.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types
from livekit import rtc

from ..audio.local_recorder import LocalConversationRecorder
from ..audio.mic_mixer import ParallelMicMixer
from ..audio.pcm_cue import load_wav_pcm, resolve_cue_asset
from ..config import SimConfig
from ..paths import package_templates_dir

if TYPE_CHECKING:
    from ..livekit.observer import Observer
    from ..logging.event_writer import EventWriter

GEMINI_IN_RATE = 16_000
GEMINI_OUT_RATE = 24_000
END_CALL_TOKEN = "[END_CALL]"


class GeminiCallerBridge:
    """Owns the Gemini Live session + the LiveKit audio tracks of the simulated caller."""

    def __init__(
        self,
        cfg: SimConfig,
        room: rtc.Room,
        observer: "Observer",
        writer: "EventWriter",
        persona_system_prompt: str,
        first_speaker: str,
        recorder: LocalConversationRecorder | None = None,
    ) -> None:
        self.cfg = cfg
        self.room = room
        self.observer = observer
        self.writer = writer
        self.persona_system_prompt = persona_system_prompt
        self.first_speaker = first_speaker
        self.recorder = recorder

        self.end_call = asyncio.Event()
        self._agent_track_queue: asyncio.Queue[rtc.RemoteAudioTrack] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._source: rtc.AudioSource | None = None
        # Parallel speech+noise into one AudioSource (single writer, multi-layer mix).
        self._mixer: ParallelMicMixer | None = None
        self._sim_out_text = ""
        self._live_session: Any | None = None
        self._suppress_output_until_mono: float | None = None
        # Scripted user long-silence: hold persona + pause dead_call until this mono time (+ grace).
        self._script_hold_until_mono: float | None = None
        self._script_hold_grace_s: float = 20.0

    # ------------------------------------------------------------------ setup

    def watch_agent_tracks(self, agent_identity: str) -> None:
        @self.room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            if p.identity == agent_identity and track.kind == rtc.TrackKind.KIND_AUDIO:
                self._agent_track_queue.put_nowait(track)

        # Track may already be subscribed before this handler attaches.
        for p in self.room.remote_participants.values():
            if p.identity != agent_identity:
                continue
            for pub in p.track_publications.values():
                if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._agent_track_queue.put_nowait(pub.track)

    async def publish_mic(self) -> rtc.AudioSource:
        self._source = rtc.AudioSource(GEMINI_OUT_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("lk-sim-mic", self._source)
        await self.room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        if self.recorder is not None:
            self.recorder.mark_start()
        # Mixer owns capture_frame; speech (Gemini) + noise (room_pcm) mix in parallel.
        self._mixer = ParallelMicMixer(
            self._source,
            sample_rate=GEMINI_OUT_RATE,
            recorder=self.recorder,
        )
        self._mixer.start()
        self.writer.emit(
            "sim.mic_published",
            spec={"sample_rate": GEMINI_OUT_RATE, "mixer": "parallel"},
            source="sim",
            include_dialogue=False,
        )
        return self._source

    # -------------------------------------------------------------------- run

    async def run(self) -> None:
        client = genai.Client(api_key=self.cfg.simulator.google_api_key)
        voice = self.cfg.simulator.voice

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],  # AUDIO only — TEXT → 1011 close
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice.voice)
                ),
                language_code=voice.language,
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=self.persona_system_prompt)]
            ),
        )

        source = await self.publish_mic()

        async with client.aio.live.connect(model=voice.model, config=config) as session:
            self._live_session = session
            self.writer.emit(
                "sim.gemini_connected",
                spec={"model": voice.model, "voice": voice.voice, "language": voice.language},
                source="sim",
                include_dialogue=False,
            )
            if self.first_speaker == "user":
                await session.send_realtime_input(
                    text="(The call just connected. You speak first, per your instructions.)"
                )

            self._tasks = [
                asyncio.create_task(self._pump_agent_audio(session), name="agent->gemini"),
                asyncio.create_task(self._pump_gemini_events(session, source), name="gemini->lk"),
            ]
            try:
                await self.end_call.wait()
            finally:
                self._live_session = None
                for t in self._tasks:
                    t.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
                if self._mixer is not None:
                    await self._mixer.aclose()
                    self._mixer = None

    def stop(self) -> None:
        self.end_call.set()
        if self._mixer is not None:
            self._mixer.stop()

    def suppress_persona_output(self, duration_ms: int) -> None:
        """Block Gemini audio/text to the room after a scripted PCM cue (caller silence)."""
        if duration_ms <= 0:
            return
        until = time.monotonic() + duration_ms / 1000
        prev = self._suppress_output_until_mono
        self._suppress_output_until_mono = until if prev is None else max(prev, until)

    def begin_scripted_user_silence(self, duration_ms: int, *, grace_s: float = 20.0) -> None:
        """Mark intentional user silence so dead_call does not kill mid-hold / early recovery."""
        if duration_ms <= 0:
            return
        until = time.monotonic() + duration_ms / 1000
        prev = self._script_hold_until_mono
        self._script_hold_until_mono = until if prev is None else max(prev, until)
        self._script_hold_grace_s = max(self._script_hold_grace_s, float(grace_s))
        self.suppress_persona_output(duration_ms)

    def scripted_silence_active(self) -> bool:
        """True while scripted silence is holding or within post-hold grace (agent may re-engage)."""
        if self._script_hold_until_mono is None:
            return False
        grace = self._script_hold_grace_s
        if time.monotonic() <= self._script_hold_until_mono + grace:
            return True
        self._script_hold_until_mono = None
        return False

    def _persona_output_suppressed(self) -> bool:
        if self._suppress_output_until_mono is None:
            return False
        if time.monotonic() >= self._suppress_output_until_mono:
            self._suppress_output_until_mono = None
            return False
        return True

    async def inject_cue(
        self,
        text: str,
        *,
        label: str = "script",
        delivery: str = "gemini_text",
        asset: str | None = None,
        scenario_dir: Path | None = None,
    ) -> None:
        """Inject caller speech while the agent is talking."""
        if delivery == "room_pcm":
            if self._mixer is None or self._source is None:
                raise RuntimeError("Sim mic/mixer not ready — cannot play room_pcm cue")
            if not asset:
                raise ValueError("room_pcm cue requires asset")
            wav_path = resolve_cue_asset(
                asset,
                scenario_dir=scenario_dir,
                templates_dir=package_templates_dir(),
            )
            pcm, rate, channels = load_wav_pcm(wav_path)
            if channels != 1:
                raise ValueError("Only mono room_pcm assets are supported")
            if rate != GEMINI_OUT_RATE:
                raise ValueError(
                    f"room_pcm asset rate {rate} != sim mic {GEMINI_OUT_RATE} "
                    f"(resample cue WAV): {wav_path}"
                )
            # Parallel: noise layer mixes with Gemini speech; does not mute TTS.
            self._mixer.push_noise(pcm)
            # Pace script step roughly for the noise duration without blocking speech path.
            duration_s = max(0.05, len(pcm) / 2 / rate)
            await asyncio.sleep(duration_s)
            self.writer.emit(
                "sim.script_inject",
                spec={
                    "text": text,
                    "label": label,
                    "delivery": delivery,
                    "asset": str(wav_path),
                    "mix": "parallel",
                    "duration_ms": int(duration_s * 1000),
                },
                source="script",
                include_dialogue=False,
            )
            return

        if self._live_session is None:
            raise RuntimeError("Gemini live session not ready for inject")
        await self._live_session.send_realtime_input(text=text)
        self.writer.emit(
            "sim.script_inject",
            spec={"text": text, "label": label, "delivery": delivery},
            source="script",
            include_dialogue=False,
        )

    # -------------------------------------------------------- agent -> gemini

    async def _pump_agent_audio(self, session: genai.live.AsyncSession) -> None:
        """Forward the agent's audio track (resampled to 16k) into Gemini."""
        while True:
            track = await self._agent_track_queue.get()
            self.writer.emit(
                "sim.agent_audio_bridged",
                spec={"track_sid": track.sid},
                source="sim",
                include_dialogue=False,
            )
            stream = rtc.AudioStream(track, sample_rate=GEMINI_IN_RATE, num_channels=1)
            try:
                async for frame_event in stream:
                    frame = frame_event.frame
                    pcm = bytes(frame.data)
                    if self.recorder is not None:
                        self.recorder.push_agent(pcm, GEMINI_IN_RATE)
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=pcm,
                            mime_type=f"audio/pcm;rate={GEMINI_IN_RATE}",
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.writer.emit(
                    "sim.error",
                    spec={"where": "agent->gemini", "error": f"{type(e).__name__}: {e}"},
                    source="sim",
                    include_dialogue=False,
                )
            finally:
                await stream.aclose()

    # -------------------------------------------------------- gemini -> livekit

    async def _pump_gemini_events(
        self, session: genai.live.AsyncSession, source: rtc.AudioSource
    ) -> None:
        """Play Gemini audio into the room; log transcriptions and interruptions."""
        try:
            while not self.end_call.is_set():
                async for response in session.receive():
                    sc = response.server_content
                    if sc is None:
                        continue

                    if sc.interrupted:
                        self.writer.emit(
                            "interruption",
                            spec={"by": "agent", "note": "Gemini output interrupted by agent audio"},
                            source="sim",
                        )

                    # Caller-side transcriptions: what the sim heard itself say (output)
                    # and what it heard from the agent (input).
                    if sc.output_transcription and sc.output_transcription.text:
                        if not self._persona_output_suppressed():
                            self._sim_out_text += sc.output_transcription.text
                            self.observer.on_transcript(
                                "user",
                                self._sim_out_text.replace(END_CALL_TOKEN, "").strip(),
                                final=False,
                                source="sim.gemini",
                            )
                    if sc.input_transcription and sc.input_transcription.text:
                        # Agent speech as heard by the sim. lk.transcription is the primary
                        # agent transcript source; keep this as a low-priority mirror.
                        self.writer.emit(
                            "sim.heard_agent",
                            spec={"text": sc.input_transcription.text},
                            source="sim.gemini",
                        )

                    if sc.model_turn:
                        for part in sc.model_turn.parts or []:
                            blob = part.inline_data
                            if blob and blob.data and not self._persona_output_suppressed():
                                await self._play_pcm(blob.data)

                    if sc.turn_complete:
                        if self._persona_output_suppressed():
                            self._sim_out_text = ""
                            continue
                        text = self._sim_out_text.strip()
                        if text:
                            ended = END_CALL_TOKEN in text
                            clean = text.replace(END_CALL_TOKEN, "").strip()
                            if clean:
                                self.observer.on_transcript(
                                    "user", clean, final=True, source="sim.gemini"
                                )
                            self._sim_out_text = ""
                            if ended:
                                self.writer.emit(
                                    "sim.end_call_token",
                                    spec={"text": clean},
                                    source="sim.gemini",
                                )
                                self.end_call.set()
                                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.writer.emit(
                "sim.error",
                spec={"where": "gemini->lk", "error": f"{type(e).__name__}: {e}"},
                source="sim",
                include_dialogue=False,
            )
            self.end_call.set()

    async def _play_pcm(self, pcm: bytes) -> None:
        """Queue Gemini TTS onto the parallel mixer (mixes with active noise layers)."""
        if not pcm:
            return
        if self._mixer is not None:
            self._mixer.push_speech(pcm)
            return
        # Fallback if mixer not started (should not happen after publish_mic).
        source = self._source
        if source is None:
            return
        samples = len(pcm) // 2
        if samples == 0:
            return
        if self.recorder is not None:
            self.recorder.push_sim(pcm, GEMINI_OUT_RATE)
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=GEMINI_OUT_RATE,
            num_channels=1,
            samples_per_channel=samples,
        )
        await source.capture_frame(frame)
