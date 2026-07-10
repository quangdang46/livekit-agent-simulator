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

from ..audio.pcm_cue import load_wav_pcm, play_pcm_to_source, resolve_cue_asset
from ..config import SimConfig

if TYPE_CHECKING:
    from ..livekit.observer import Observer
    from ..logging.event_writer import EventWriter

GEMINI_IN_RATE = 16_000
GEMINI_OUT_RATE = 24_000
END_CALL_TOKEN = "[END_CALL]"
_REPO_ROOT = Path(__file__).resolve().parents[3]


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
    ) -> None:
        self.cfg = cfg
        self.room = room
        self.observer = observer
        self.writer = writer
        self.persona_system_prompt = persona_system_prompt
        self.first_speaker = first_speaker

        self.end_call = asyncio.Event()
        self._agent_track_queue: asyncio.Queue[rtc.RemoteAudioTrack] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._source: rtc.AudioSource | None = None
        self._sim_out_text = ""
        self._live_session: Any | None = None

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
        self.writer.emit(
            "sim.mic_published",
            spec={"sample_rate": GEMINI_OUT_RATE},
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

    def stop(self) -> None:
        self.end_call.set()

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
            if self._source is None:
                raise RuntimeError("Sim mic not published — cannot play room_pcm cue")
            if not asset:
                raise ValueError("room_pcm cue requires asset")
            wav_path = resolve_cue_asset(asset, scenario_dir=scenario_dir, package_root=_REPO_ROOT)
            pcm, rate, channels = load_wav_pcm(wav_path)
            await play_pcm_to_source(self._source, pcm, sample_rate=rate, num_channels=channels)
            self.writer.emit(
                "sim.script_inject",
                spec={"text": text, "label": label, "delivery": delivery, "asset": str(wav_path)},
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
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=bytes(frame.data),
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
                            if blob and blob.data:
                                await self._play_pcm(source, blob.data)

                    if sc.turn_complete:
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

    @staticmethod
    async def _play_pcm(source: rtc.AudioSource, pcm: bytes) -> None:
        samples = len(pcm) // 2  # PCM16 mono
        if samples == 0:
            return
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=GEMINI_OUT_RATE,
            num_channels=1,
            samples_per_channel=samples,
        )
        await source.capture_frame(frame)
