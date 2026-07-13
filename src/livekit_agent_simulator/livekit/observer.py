"""Passive in-room observer — zero-touch on the agent under test.

Sources:
    L0  room events        participant joins/leaves, tracks, active speakers, disconnect
    L1a lk.transcription   text streams published by AgentSession (agent + user segments)
    L1b custom data topics any payload matching observe.transcript_payload_types (e.g. transcript_turn)
    L2  tool patterns      config-driven match rules over data-topic JSON payloads
    L3  lk.agent.session   SDK tools, state, errors, usage, and final chat history

Attribute keys per LiveKit docs (agents/multimodality/text):
    lk.transcription_final, lk.segment_id
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from livekit import rtc

from ..config import ObserveConfig, ToolEventPattern
from ..logging.event_writer import EventWriter
from .agent_session_observer import AgentSessionObserver

ATTR_FINAL = "lk.transcription_final"
ATTR_SEGMENT_ID = "lk.segment_id"

# Lower index = higher priority when deduping finals from multiple sources.
_USER_FINAL_PRIORITY = ("sim.gemini", "data", "lk.transcription")
_AGENT_FINAL_PRIORITY = ("data", "lk.transcription", "sim.gemini")


def _lookup_path(payload: dict[str, Any], dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _similar_text(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return len(shorter) / len(longer) >= 0.85
    return False


def _canonical_source(source: str) -> str:
    if source in ("sim.gemini", "lk.transcription"):
        return source
    return "data"


def _source_priority_rank(source: str, role: str) -> int:
    order = _USER_FINAL_PRIORITY if role == "user" else _AGENT_FINAL_PRIORITY
    canonical = _canonical_source(source)
    try:
        return order.index(canonical)
    except ValueError:
        return len(order)


class Observer:
    def __init__(
        self,
        room: rtc.Room,
        writer: EventWriter,
        observe: ObserveConfig,
        agent_identity: str,
        sim_identity: str,
        *,
        first_speaker: str = "agent",
    ) -> None:
        self.room = room
        self.writer = writer
        self.observe = observe
        self.agent_identity = agent_identity
        self.sim_identity = sim_identity
        self.first_speaker = first_speaker

        # Turn tracking: a turn = one user utterance + the agent reply to it.
        self.turn = 0
        self._last_user_final_mono: float | None = None
        self._agent_replied_this_turn = False
        self.last_agent_activity_mono: float = time.monotonic()
        self.agent_disconnected = asyncio.Event()
        self._agent_has_spoken = False
        self._user_has_spoken = False
        self._current_turn_user_norm: str | None = None

        self.agent_is_active_speaker = False
        self._agent_active_since_mono: float | None = None

        # (role, normalized text) -> (source, monotonic time)
        self._recent_finals: dict[tuple[str, str], tuple[str, float]] = {}

        # tool.start events waiting for their tool.end/tool.error (call_id -> event)
        self._open_tools: dict[str, dict[str, Any]] = {}
        self._agent_session = (
            AgentSessionObserver(room, writer, agent_identity)
            if observe.lk_agent_session
            else None
        )

    @property
    def agent_replied_this_turn(self) -> bool:
        return self._agent_replied_this_turn

    @property
    def agent_has_spoken(self) -> bool:
        return self._agent_has_spoken

    @property
    def user_has_spoken(self) -> bool:
        return self._user_has_spoken

    def agent_active_duration_ms(self) -> int | None:
        if self._agent_active_since_mono is None:
            return None
        return int((time.monotonic() - self._agent_active_since_mono) * 1000)

    # ------------------------------------------------------------------ attach

    def attach(self) -> None:
        room = self.room

        @room.on("participant_connected")
        def _on_join(p: rtc.RemoteParticipant) -> None:
            self.writer.emit(
                "room.participant_connected",
                spec={"identity": p.identity, "name": p.name, "kind": str(p.kind)},
                source="room",
                include_dialogue=False,
            )

        @room.on("participant_disconnected")
        def _on_leave(p: rtc.RemoteParticipant) -> None:
            self.writer.emit(
                "room.participant_disconnected",
                spec={"identity": p.identity},
                source="room",
                include_dialogue=False,
            )
            if p.identity == self.agent_identity:
                self.agent_disconnected.set()

        @room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            self.writer.emit(
                "room.track_subscribed",
                spec={"identity": p.identity, "kind": str(track.kind), "sid": track.sid},
                source="room",
                include_dialogue=False,
            )

        @room.on("active_speakers_changed")
        def _on_speakers(speakers: list[rtc.Participant]) -> None:
            identities = [s.identity for s in speakers]
            agent_now = self.agent_identity in identities
            if agent_now and not self.agent_is_active_speaker:
                self._agent_active_since_mono = time.monotonic()
            elif not agent_now:
                self._agent_active_since_mono = None
            self.agent_is_active_speaker = agent_now
            if agent_now:
                self.last_agent_activity_mono = time.monotonic()
            self.writer.emit(
                "room.active_speakers",
                spec={"identities": identities},
                source="room",
                include_dialogue=False,
            )

        @room.on("disconnected")
        def _on_disconnected(*args: object) -> None:
            self.writer.emit(
                "room.disconnected", spec={}, source="room", include_dialogue=False
            )
            self.agent_disconnected.set()

        @room.on("data_received")
        def _on_data(packet: rtc.DataPacket) -> None:
            topic = packet.topic or ""
            if self.observe.data_topics and topic not in self.observe.data_topics:
                return
            self._handle_data_topic(topic, packet)

        if self.observe.lk_transcription:
            room.register_text_stream_handler("lk.transcription", self._on_transcription_stream)
        if self._agent_session is not None:
            self._agent_session.attach()

    async def finalize_session_snapshot(self) -> None:
        if self._agent_session is not None:
            await self._agent_session.fetch_session_snapshot()

    async def detach(self) -> None:
        if self._agent_session is not None:
            await self._agent_session.detach()

    # ------------------------------------------------------------- transcription

    def _on_transcription_stream(self, reader: Any, participant_identity: str) -> None:
        asyncio.ensure_future(self._read_transcription(reader, participant_identity))

    async def _read_transcription(self, reader: Any, participant_identity: str) -> None:
        try:
            text = await reader.read_all()
        except Exception as e:
            self.writer.emit(
                "observer.error",
                spec={"where": "lk.transcription", "error": f"{type(e).__name__}: {e}"},
                source="lk.transcription",
                include_dialogue=False,
            )
            return
        attrs = dict(getattr(reader.info, "attributes", {}) or {})
        final = attrs.get(ATTR_FINAL, "").lower() == "true"
        segment_id = attrs.get(ATTR_SEGMENT_ID)
        if not text.strip():
            return

        role = "agent" if participant_identity == self.agent_identity else "user"
        self.on_transcript(role, text, final, segment_id=segment_id, source="lk.transcription")

    def _accept_final(self, role: str, text: str, source: str) -> bool:
        """Drop duplicate finals from lower-priority sources within the dedupe window."""
        norm = _normalize_text(text)
        if not norm:
            return False
        key = (role, norm)
        now = time.monotonic()
        window_s = self.observe.transcript_dedupe_window_ms / 1000
        prev = self._recent_finals.get(key)
        if prev is not None:
            prev_source, prev_mono = prev
            if now - prev_mono <= window_s:
                if _source_priority_rank(source, role) >= _source_priority_rank(prev_source, role):
                    return False
        self._recent_finals[key] = (source, now)
        return True

    # Shared entry point — also used by the Gemini bridge (sim-side transcription).
    def on_transcript(
        self,
        role: str,
        text: str,
        final: bool,
        segment_id: str | None = None,
        source: str = "lk.transcription",
    ) -> None:
        now_ms = int(time.time() * 1000)
        self.writer.update_dialogue(role, text, final, at_ms=now_ms)

        spec: dict[str, Any] = {"text": text, "final": final}
        if segment_id:
            spec["segment_id"] = segment_id

        if role == "agent":
            self.last_agent_activity_mono = time.monotonic()
            if final:
                self._agent_has_spoken = True

        if not final:
            self.writer.emit(f"transcript.{role}.interim", spec=spec, source=source)
            return

        if not self._accept_final(role, text, source):
            return

        if role == "user":
            norm = _normalize_text(text)
            if self._last_user_final_mono is not None and not self._agent_replied_this_turn:
                self.writer.emit(
                    "transcript.user.final",
                    spec={**spec, "same_turn": True},
                    source=source,
                )
                return
            if self._agent_replied_this_turn and (
                norm == self._current_turn_user_norm
                or _similar_text(norm, self._current_turn_user_norm or "")
            ):
                return
            self._user_has_spoken = True
            self.turn += 1
            self._current_turn_user_norm = norm
            self.writer.begin_turn(self.turn)
            self._last_user_final_mono = time.monotonic()
            self._agent_replied_this_turn = False
            self.writer.emit("transcript.user.final", spec=spec, source=source)
        else:
            if self.turn == 0 and self.first_speaker == "user" and not self._user_has_spoken:
                self.writer.emit(
                    "transcript.agent.preamble",
                    spec={**spec, "note": "agent spoke before user; not counted as a turn"},
                    source=source,
                )
                return
            if self.turn == 0:
                self.turn = 1
                self.writer.begin_turn(self.turn)
            if not self._agent_replied_this_turn and self._last_user_final_mono is not None:
                spec["turn_taking_ms"] = int(
                    (time.monotonic() - self._last_user_final_mono) * 1000
                )
            self._agent_replied_this_turn = True
            self.writer.emit("transcript.agent.final", spec=spec, source=source)

    # --------------------------------------------------------------- data topics

    def _handle_data_topic(self, topic: str, packet: rtc.DataPacket) -> None:
        sender = packet.participant.identity if packet.participant else None
        try:
            payload = json.loads(packet.data.decode("utf-8"))
        except Exception:
            self.writer.emit(
                "data.raw",
                spec={"topic": topic, "bytes": len(packet.data), "sender": sender},
                source=topic or "data",
                include_dialogue=False,
            )
            return

        emitted_tool = self._match_tool_patterns(topic, payload)
        parsed = self._parse_transcript_payload(payload)
        if parsed is not None:
            role, text = parsed
            self.on_transcript(role, text, final=True, source=topic or "data")
            return
        if not emitted_tool:
            self.writer.emit(
                "data.message",
                spec={"topic": topic, "sender": sender, "payload": payload},
                source=topic or "data",
            )

    def _parse_transcript_payload(self, payload: dict[str, Any]) -> tuple[str, str] | None:
        """Generic transcript_turn shape — any data topic, not hardcoded to one worker."""
        if payload.get("type") not in self.observe.transcript_payload_types:
            return None
        if payload.get("interim"):
            return None
        turn = payload.get("turn")
        if not isinstance(turn, dict):
            return None
        role = turn.get("role")
        text = turn.get("text")
        if role not in ("user", "agent") or not isinstance(text, str) or not text.strip():
            return None
        return role, text.strip()

    def _match_tool_patterns(self, topic: str, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for pattern in self.observe.tool_event_patterns:
            if self._pattern_matches(pattern, topic, payload):
                self._emit_tool_event(pattern.emit, topic, payload)
                return True
        return False

    @staticmethod
    def _pattern_matches(pattern: ToolEventPattern, topic: str, payload: dict[str, Any]) -> bool:
        for key, expected in pattern.match.items():
            if key == "topic":
                if topic != expected:
                    return False
                continue
            if _lookup_path(payload, key) != expected:
                return False
        return True

    def _emit_tool_event(self, emit_kind: str, topic: str, payload: dict[str, Any]) -> None:
        name = payload.get("tool") or payload.get("name") or _lookup_path(payload, "spec.name")
        call_id = (
            payload.get("call_id")
            or payload.get("toolCallId")
            or _lookup_path(payload, "spec.call_id")
        )
        spec: dict[str, Any] = {"name": name, "call_id": call_id, "payload": payload}

        if emit_kind == "tool.start":
            event = self.writer.emit("tool.start", spec=spec, source=topic)
            if call_id:
                self._open_tools[str(call_id)] = event
            return

        parent_id: str | None = None
        if call_id:
            start = self._open_tools.pop(str(call_id), None)
            if start is not None:
                parent_id = start["event_id"]
                spec["duration_ms"] = (
                    int((time.monotonic() - self.writer.run_start_mono) * 1000)
                    - start["ts_mono_ms"]
                )
        if emit_kind == "tool.error":
            spec["error"] = (
                payload.get("error") or payload.get("message") or _lookup_path(payload, "spec.error")
            )
        self.writer.emit(emit_kind, spec=spec, source=topic, parent_event_id=parent_id)
