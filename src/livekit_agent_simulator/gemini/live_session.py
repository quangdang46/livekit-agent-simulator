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
from .end_call import (
    END_CALL_TOKEN,
    contains_end_call_signal,
    strip_end_call_signal,
)

if TYPE_CHECKING:
    from ..livekit.observer import Observer
    from ..logging.event_writer import EventWriter

GEMINI_IN_RATE = 16_000
GEMINI_OUT_RATE = 24_000

__all__ = ["END_CALL_TOKEN", "GeminiCallerBridge"]


def _is_voice_cue_asset(asset: str | None) -> bool:
    """True for voice.* refs (spoken script lines), not noise.*."""
    if not asset:
        return False
    name = str(asset).strip().lower()
    if name.startswith("builtin:"):
        name = name[len("builtin:") :]
    if name.startswith("@"):
        name = name[1:]
    return name.startswith("voice.")


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
        midcall_cues: list | None = None,
    ) -> None:
        self.cfg = cfg
        self.room = room
        self.observer = observer
        self.writer = writer
        self.persona_system_prompt = persona_system_prompt
        self.first_speaker = first_speaker
        self.recorder = recorder
        # Dialog steering texts from CallerPolicy (bootstrap / reground); not PCM Script.
        self._midcall_cues = list(midcall_cues or [])

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
        # Linear gain for script-injected gemini_text playback (reset on turn_complete).
        self._inject_playback_gain: float = 1.0
        self._inject_turn_active: bool = False
        # Drop persona PCM after hang-up token / spoken "end call" is detected.
        self._mute_persona_audio = False

    # ------------------------------------------------------------------ setup

    def watch_agent_tracks(self, agent_identity: str) -> None:
        """Subscribe to a specific remote participant's audio (WebRTC agent path)."""

        def _maybe_queue(p: rtc.RemoteParticipant, track: rtc.Track) -> None:
            if p.identity == agent_identity and track.kind == rtc.TrackKind.KIND_AUDIO:
                self._agent_track_queue.put_nowait(track)

        @self.room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            _maybe_queue(p, track)

        # Track may already be subscribed before this handler attaches.
        for p in self.room.remote_participants.values():
            if p.identity != agent_identity:
                continue
            for pub in p.track_publications.values():
                if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._agent_track_queue.put_nowait(pub.track)

    def watch_sip_audio_tracks(self) -> None:
        """Subscribe to any remote SIP (or non-local) audio on sim_room (hairpin path).

        On Cloud hairpin, agent audio arrives as the SIP participant track in sim-room.
        We accept the first remote audio track that is not our own publish.
        """

        def _is_sip_like(p: rtc.RemoteParticipant) -> bool:
            kind = getattr(p, "kind", None)
            try:
                from livekit.protocol.models import ParticipantInfo

                kind_name = ParticipantInfo.Kind.Name(kind) if kind is not None else ""
                if kind_name == "SIP":
                    return True
            except Exception:
                pass
            attrs = getattr(p, "attributes", None) or {}
            if isinstance(attrs, dict) and any(str(k).startswith("sip.") for k in attrs):
                return True
            # Fallback: any remote participant audio on sim-room (hairpin leg).
            return True

        def _maybe_queue(p: rtc.RemoteParticipant, track: rtc.Track) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            if not _is_sip_like(p):
                return
            self._agent_track_queue.put_nowait(track)

        @self.room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            _maybe_queue(p, track)

        for p in self.room.remote_participants.values():
            for pub in p.track_publications.values():
                if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    _maybe_queue(p, pub.track)

    def watch_agent_tracks_on_room(
        self, room: rtc.Room, agent_identity: str
    ) -> None:
        """Subscribe to agent audio on a *different* room than Gemini mic (SIP 2-room).

        Outbound hairpin often never places a SIP track in sim-room (same DID as
        agent inbound). Gemini still needs agent PCM to continue the conversation —
        feed it from agent-room where the worker publishes WebRTC audio.
        """

        def _maybe_queue(p: rtc.RemoteParticipant, track: rtc.Track) -> None:
            if p.identity != agent_identity:
                return
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            self._agent_track_queue.put_nowait(track)

        @room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            _maybe_queue(p, track)

        for p in room.remote_participants.values():
            if p.identity != agent_identity:
                continue
            for pub in p.track_publications.values():
                if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._agent_track_queue.put_nowait(pub.track)

        self.writer.emit(
            "sim.agent_listen_room",
            spec={
                "agent_identity": agent_identity,
                "listen": "agent_room",
                "note": "Gemini ears on agent-room WebRTC (sim-room SIP track missing)",
            },
            source="sim",
            include_dialogue=False,
        )

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
            await self._emit_bootstrap_cues(session)

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


    async def _emit_bootstrap_cues(self, session: Any) -> None:
        """Send policy bootstrap texts (first speaker). Reground cues are on-demand."""
        for cue in self._midcall_cues:
            kind = getattr(cue, "kind", "") or ""
            if kind != "bootstrap":
                continue
            text = str(getattr(cue, "text", "") or "").strip()
            if not text:
                continue
            await session.send_realtime_input(text=text)
            self.writer.emit(
                "sim.caller_midcall",
                spec={
                    "kind": kind,
                    "label": getattr(cue, "label", None),
                    "text": text[:240],
                },
                source="sim",
                include_dialogue=False,
            )

    async def inject_reground(self, *, label: str | None = None) -> None:
        """Inject the first reground MidcallCue (goal focus). No-op if none / session down."""
        if self._live_session is None:
            return
        for cue in self._midcall_cues:
            if getattr(cue, "kind", "") != "reground":
                continue
            text = str(getattr(cue, "text", "") or "").strip()
            if not text:
                continue
            await self._live_session.send_realtime_input(text=text)
            self.writer.emit(
                "sim.caller_midcall",
                spec={
                    "kind": "reground",
                    "label": label or getattr(cue, "label", None),
                    "text": text[:240],
                },
                source="sim",
                include_dialogue=False,
            )
            return

    def stop(self) -> None:
        self.end_call.set()
        if self._mixer is not None:
            self._mixer.stop()

    def sim_hang_up(self) -> None:
        """Called by ScriptRunner action=hang_up : hard disconnect from the room."""
        self.writer.emit(
            "sim.hang_up",
            spec={"source": "script", "by": "sim"},
            source="sim",
            include_dialogue=False,
        )
        self.end_call.set()

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
        gain: float = 1.0,
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
                project_root=self.cfg.project_root,
                cues_config=getattr(self.cfg, "cues", None),
            )
            pcm, rate, channels = load_wav_pcm(wav_path)
            if channels != 1:
                raise ValueError("Only mono room_pcm assets are supported")
            if rate != GEMINI_OUT_RATE:
                raise ValueError(
                    f"room_pcm asset rate {rate} != sim mic {GEMINI_OUT_RATE} "
                    f"(resample cue WAV): {wav_path}"
                )
            duration_s = max(0.05, len(pcm) / 2 / rate)
            # Vocal speech (voice.*): play on speech layer + suppress free persona TTS so
            # goodbye/[END_CALL] cannot override the scripted words (SoT = mic audio).
            # Noise layers stay on push_noise so they can ride under persona speech.
            vocal = _is_voice_cue_asset(asset)
            if vocal:
                self.suppress_persona_output(int(duration_s * 1000) + 400)
                self._mixer.push_speech(pcm, gain=gain)
                mix = "speech"
            else:
                self._mixer.push_noise(pcm, gain=gain)
                mix = "parallel"
            await asyncio.sleep(duration_s)
            self.writer.emit(
                "sim.script_inject",
                spec={
                    "text": text,
                    "label": label,
                    "delivery": delivery,
                    "asset": str(wav_path),
                    "mix": mix,
                    "duration_ms": int(duration_s * 1000),
                    "gain": gain,
                },
                source="script",
                include_dialogue=False,
            )
            return

        if self._live_session is None:
            raise RuntimeError("Gemini live session not ready for inject")
        # Prefer room_pcm + voice.* WAVs when asserts require exact spoken interrupt words.
        # gemini_text is realtime input to the persona — not guaranteed literal TTS.
        self._inject_playback_gain = gain
        self._inject_turn_active = True
        await self._live_session.send_realtime_input(text=text)
        self.writer.emit(
            "sim.script_inject",
            spec={"text": text, "label": label, "delivery": delivery, "gain": gain},
            source="script",
            include_dialogue=False,
        )

    # -------------------------------------------------------- agent -> gemini

    async def _pump_agent_audio(self, session: genai.live.AsyncSession) -> None:
        """Forward the agent's audio track (resampled to 16k) into Gemini.

        Recording of agent audio prefers Observer on agent-room (see run_orchestrator).
        We still push_agent here as a fallback for single-room WebRTC when Observer
        and bridge share the same track path (duplicate pushes are fine — wall-clock
        recorder pads gaps; overlapping audio is rare because only one pump runs).
        """
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
                    # R-channel: Observer records from agent-room when attached with
                    # recorder. Fallback here only if Observer is not recording any track
                    # (single-room WebRTC still works if observer record fails to start).
                    obs_recording = bool(
                        getattr(self.observer, "_recording_track_sids", None)
                    )
                    if self.recorder is not None and not obs_recording:
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
                            if contains_end_call_signal(self._sim_out_text):
                                self._mute_hang_up_audio()
                            self.observer.on_transcript(
                                "user",
                                strip_end_call_signal(self._sim_out_text),
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
                            if (
                                blob
                                and blob.data
                                and not self._persona_output_suppressed()
                                and not self._mute_persona_audio
                            ):
                                await self._play_pcm(blob.data)

                    if sc.turn_complete:
                        self._inject_turn_active = False
                        self._inject_playback_gain = 1.0
                        if self._persona_output_suppressed():
                            self._sim_out_text = ""
                            self._mute_persona_audio = False
                            continue
                        text = self._sim_out_text.strip()
                        if text:
                            ended = contains_end_call_signal(text)
                            clean = strip_end_call_signal(text)
                            if clean:
                                self.observer.on_transcript(
                                    "user", clean, final=True, source="sim.gemini"
                                )
                            self._sim_out_text = ""
                            if ended:
                                # Stop new hang-up chatter, but drain goodbye already queued
                                # so the recorder / room do not lose the last spoken sentence.
                                self._mute_persona_audio = True
                                await self._drain_persona_speech(timeout_s=3.0)
                                self.writer.emit(
                                    "sim.end_call_token",
                                    spec={"text": clean},
                                    source="sim.gemini",
                                )
                                self.end_call.set()
                                return
                            self._mute_persona_audio = False
                        else:
                            self._mute_persona_audio = False
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

    def _mute_hang_up_audio(self) -> None:
        """Stop queueing further hang-up chatter; keep goodbye already buffered."""
        self._mute_persona_audio = True

    async def _drain_persona_speech(self, *, timeout_s: float = 3.0) -> None:
        if self._mixer is not None:
            await self._mixer.wait_speech_drain(timeout_s=timeout_s)
            return
        # Fallback path has no queue — small settle for in-flight capture_frame.
        await asyncio.sleep(min(0.35, timeout_s))

    async def _play_pcm(self, pcm: bytes) -> None:
        """Queue Gemini TTS onto the parallel mixer (mixes with active noise layers)."""
        if not pcm or self._mute_persona_audio:
            return
        gain = self._inject_playback_gain if self._inject_turn_active else 1.0
        if self._mixer is not None:
            self._mixer.push_speech(pcm, gain=gain)
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
