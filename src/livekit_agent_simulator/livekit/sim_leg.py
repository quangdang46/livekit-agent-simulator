"""SimLeg Strategy — transport legs for WebRTC / inbound SIP / outbound SIP.

Template Method: ``run_orchestrator`` owns the 7-phase pipeline.
Strategy: each mode implements ``connect()`` and returns a normalized ``SimLegHandle``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from livekit import rtc

from .adapter import SIM_IDENTITY, LiveKitAdapter, room_name_for_run

if TYPE_CHECKING:
    from ..config import SimConfig
    from ..logging.event_writer import EventWriter
    from ..scenario import Scenario


class SimLegError(Exception):
    """Raised when a transport leg cannot connect (dial fail, missing config, etc.)."""


@dataclass
class SimLegHandle:
    """Normalized connect result — later phases do not branch on mode."""

    agent_room: rtc.Room
    sim_room: rtc.Room
    agent_room_name: str
    sim_room_name: str
    sim_identity: str
    agent_identity: str
    mode: str
    # Agent audio for Gemini: identity in sim_room to subscribe (WebRTC agent or SIP bridge).
    gemini_listen_identity: str | None = None
    # Prefer any SIP audio on sim_room when True (outbound/inbound hairpin).
    gemini_listen_sip: bool = False
    rooms_to_delete: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    async def disconnect_rooms(self) -> None:
        for room in {id(self.agent_room): self.agent_room, id(self.sim_room): self.sim_room}.values():
            try:
                await room.disconnect()
            except Exception:
                pass


@dataclass
class SimLegContext:
    adapter: LiveKitAdapter
    cfg: SimConfig
    scenario: Scenario
    writer: EventWriter
    run_id: str
    dispatch_metadata: str | None
    first_speaker: str


class SimLeg(Protocol):
    async def connect(self, ctx: SimLegContext) -> SimLegHandle: ...


def sim_leg_factory(mode: str) -> SimLeg:
    """Map ``Caller.mode`` → strategy instance."""
    m = (mode or "webrtc_sim").strip().lower()
    if m == "webrtc_sim":
        return WebRtcSimLeg()
    if m == "outbound_sip":
        return OutboundSipSimLeg()
    if m == "inbound_sip":
        return InboundSipSimLeg()
    if m == "agent_dials":
        return AgentDialsSimLeg()
    raise SimLegError(
        f"Unknown Caller.mode {mode!r}. "
        f"Expected webrtc_sim | inbound_sip | outbound_sip | agent_dials."
    )


# ── WebRTC ───────────────────────────────────────────────────────────────────


class WebRtcSimLeg:
    """Single-room: Gemini WebRTC + agent (default / backward compatible)."""

    async def connect(self, ctx: SimLegContext) -> SimLegHandle:
        adapter, writer = ctx.adapter, ctx.writer
        dispatch = await adapter.create_room_and_dispatch(ctx.run_id, ctx.dispatch_metadata)
        writer.emit(
            "dispatch.created",
            spec={
                "room": dispatch.room_name,
                "agent_name": ctx.cfg.livekit.agent_name,
                "dispatch_id": dispatch.dispatch_id,
                "metadata_set": bool(ctx.dispatch_metadata),
                "mode": "webrtc_sim",
            },
            include_dialogue=False,
        )
        agent_identity = await adapter.wait_for_agent(dispatch.room_name)
        writer.emit(
            "dispatch.agent_joined",
            spec={"identity": agent_identity, "mode": "webrtc_sim"},
            include_dialogue=False,
        )
        room = await adapter.connect_simulator(dispatch.room_name)
        writer.emit(
            "sim.connected",
            spec={"identity": SIM_IDENTITY, "room": dispatch.room_name, "mode": "webrtc_sim"},
            include_dialogue=False,
        )
        return SimLegHandle(
            agent_room=room,
            sim_room=room,
            agent_room_name=dispatch.room_name,
            sim_room_name=dispatch.room_name,
            sim_identity=SIM_IDENTITY,
            agent_identity=agent_identity,
            mode="webrtc_sim",
            gemini_listen_identity=agent_identity,
            gemini_listen_sip=False,
            rooms_to_delete=[dispatch.room_name],
            meta={"dispatch_id": dispatch.dispatch_id},
        )


# ── Outbound SIP (Gemini = callee) ───────────────────────────────────────────


class OutboundSipSimLeg:
    """Agent-room dials ``call_to``; Gemini answers on sim-room (Cloud hairpin)."""

    async def connect(self, ctx: SimLegContext) -> SimLegHandle:
        from ..scenario import effective_telephony

        tel = effective_telephony(ctx.scenario, ctx.cfg)
        if not tel.outbound_trunk_id:
            raise SimLegError(
                "outbound_sip requires telephony.outbound_trunk_id "
                "(config) or Telephony.sip_trunk_id (scenario)."
            )
        if not tel.call_to:
            raise SimLegError(
                "outbound_sip requires Telephony.call_to or config telephony.sim_inbound_number "
                "(DID/number Gemini answers)."
            )

        adapter, writer = ctx.adapter, ctx.writer
        sim_room_name = f"lk-sim-sip-{ctx.run_id}"
        agent_room_name = room_name_for_run(ctx.run_id)

        await adapter.create_room(sim_room_name)
        await adapter.create_room(agent_room_name)
        writer.emit(
            "dispatch.created",
            spec={
                "room": agent_room_name,
                "sim_room": sim_room_name,
                "agent_name": ctx.cfg.livekit.agent_name,
                "mode": "outbound_sip",
                "metadata_set": bool(ctx.dispatch_metadata),
            },
            include_dialogue=False,
        )

        # Gemini joins sim-room first so it is ready when the SIP leg lands.
        sim_room = await adapter.connect_participant(
            sim_room_name, identity=SIM_IDENTITY, name="Agent Simulator Caller"
        )
        writer.emit(
            "sim.connected",
            spec={"identity": SIM_IDENTITY, "room": sim_room_name, "mode": "outbound_sip"},
            include_dialogue=False,
        )

        dispatch_id = await adapter.dispatch_agent(agent_room_name, ctx.dispatch_metadata)
        agent_identity = await adapter.wait_for_agent(agent_room_name)
        writer.emit(
            "dispatch.agent_joined",
            spec={
                "identity": agent_identity,
                "mode": "outbound_sip",
                "dispatch_id": dispatch_id,
            },
            include_dialogue=False,
        )

        prepare_ms = tel.prepare_ms
        if prepare_ms > 0:
            writer.emit(
                "outbound.prepare",
                spec={"prepare_ms": prepare_ms},
                include_dialogue=False,
            )
            await asyncio.sleep(prepare_ms / 1000)

        # Join agent-room as observer *before* dial so we subscribe to agent audio
        # from the first greeting. LiveKit does not buffer — late join misses PCM
        # (see livekit#2827 / agents#5932: subscriber must be ready before publisher).
        agent_room = await adapter.connect_participant(
            agent_room_name,
            identity=f"lk-sim-obs-{ctx.run_id[:8]}",
            name="Agent Simulator Observer",
        )
        writer.emit(
            "sim.observer_joined",
            spec={
                "room": agent_room_name,
                "mode": "outbound_sip",
                "note": "observer joined before dial to capture agent greeting",
            },
            include_dialogue=False,
        )
        # Brief settle for auto-subscribe / DTLS before media starts.
        await asyncio.sleep(0.25)

        sip_identity = f"sip-out-{ctx.run_id[:12]}"
        writer.emit(
            "outbound.dial_started",
            spec={
                "call_to": tel.call_to,
                "trunk_id_set": True,
                "room": agent_room_name,
                "participant_identity": sip_identity,
                "wait_until_answered": tel.wait_until_answered,
            },
            include_dialogue=False,
        )
        t0 = time.monotonic()
        try:
            sip_info = await adapter.create_sip_participant(
                room_name=agent_room_name,
                sip_trunk_id=tel.outbound_trunk_id,
                sip_call_to=tel.call_to,
                participant_identity=sip_identity,
                participant_name="Simulated Callee",
                wait_until_answered=tel.wait_until_answered,
                krisp_enabled=tel.krisp_enabled,
            )
        except Exception as e:
            writer.emit(
                "outbound.dial_failed",
                spec=_sip_error_spec(e, call_to=tel.call_to),
                include_dialogue=False,
            )
            raise SimLegError(f"outbound dial failed: {e}") from e

        dial_ms = int((time.monotonic() - t0) * 1000)
        sip_part_id = getattr(sip_info, "participant_identity", None) or sip_identity
        writer.emit(
            "outbound.dial_answered",
            spec={
                "call_to": tel.call_to,
                "dial_ms": dial_ms,
                "participant_identity": sip_part_id,
                "sip_call_id": getattr(sip_info, "sip_call_id", None),
            },
            include_dialogue=False,
        )
        writer.emit(
            "sip.participant_connected",
            spec={
                "identity": sip_part_id,
                "room": agent_room_name,
                "role": "outbound_leg",
            },
            include_dialogue=False,
        )

        # Wait for inbound SIP leg on sim-room (hairpin answer side), best-effort.
        # Keep this short — do not block recording/Gemini for 15s when DID is not
        # provisioned for sim-room (common when call_to == agent inbound number).
        try:
            sim_sip_id = await adapter.wait_for_sip_participant(
                sim_room_name, timeout_ms=min(3_000, max(1_500, tel.prepare_ms))
            )
            writer.emit(
                "sip.participant_connected",
                spec={"identity": sim_sip_id, "room": sim_room_name, "role": "sim_inbound_leg"},
                include_dialogue=False,
            )
        except Exception as e:
            writer.emit(
                "sip.wait_sim_leg",
                spec={
                    "status": "timeout_or_missing",
                    "detail": f"{type(e).__name__}: {e}",
                    "note": "Ensure sim inbound DID dispatch rule targets this sim-room "
                    f"({sim_room_name}) for full hairpin audio. Gemini/record use agent-room.",
                },
                include_dialogue=False,
            )

        return SimLegHandle(
            agent_room=agent_room,
            sim_room=sim_room,
            agent_room_name=agent_room_name,
            sim_room_name=sim_room_name,
            sim_identity=sip_part_id,
            agent_identity=agent_identity,
            mode="outbound_sip",
            gemini_listen_identity=None,
            gemini_listen_sip=True,
            rooms_to_delete=[agent_room_name, sim_room_name],
            meta={
                "dial_ms": dial_ms,
                "call_to": tel.call_to,
                "sip_call_id": getattr(sip_info, "sip_call_id", None),
                "dispatch_id": dispatch_id,
            },
        )


# ── Inbound SIP (Gemini = caller) ────────────────────────────────────────────


class InboundSipSimLeg:
    """Gemini in sim-room dials agent ``dial_in`` (Cloud hairpin)."""

    async def connect(self, ctx: SimLegContext) -> SimLegHandle:
        from ..scenario import effective_telephony

        tel = effective_telephony(ctx.scenario, ctx.cfg)
        if not tel.outbound_trunk_id:
            raise SimLegError(
                "inbound_sip requires telephony.outbound_trunk_id "
                "(config) or Telephony.sip_trunk_id (scenario) to place the call."
            )
        if not tel.dial_in:
            raise SimLegError(
                "inbound_sip requires Telephony.dial_in or config telephony.dial_in "
                "(agent-side inbound DID)."
            )

        adapter, writer = ctx.adapter, ctx.writer
        sim_room_name = f"lk-sim-sip-{ctx.run_id}"
        await adapter.create_room(sim_room_name)

        sim_room = await adapter.connect_participant(
            sim_room_name, identity=SIM_IDENTITY, name="Agent Simulator Caller"
        )
        writer.emit(
            "sim.connected",
            spec={"identity": SIM_IDENTITY, "room": sim_room_name, "mode": "inbound_sip"},
            include_dialogue=False,
        )

        sip_identity = f"sip-in-{ctx.run_id[:12]}"
        writer.emit(
            "inbound.dial_started",
            spec={
                "dial_in": tel.dial_in,
                "room": sim_room_name,
                "participant_identity": sip_identity,
                "wait_until_answered": tel.wait_until_answered,
            },
            include_dialogue=False,
        )
        t0 = time.monotonic()
        try:
            sip_info = await adapter.create_sip_participant(
                room_name=sim_room_name,
                sip_trunk_id=tel.outbound_trunk_id,
                sip_call_to=tel.dial_in,
                participant_identity=sip_identity,
                participant_name="Simulated Caller",
                wait_until_answered=tel.wait_until_answered,
                krisp_enabled=tel.krisp_enabled,
            )
        except Exception as e:
            writer.emit(
                "inbound.dial_failed",
                spec=_sip_error_spec(e, call_to=tel.dial_in),
                include_dialogue=False,
            )
            raise SimLegError(f"inbound dial failed: {e}") from e

        dial_ms = int((time.monotonic() - t0) * 1000)
        writer.emit(
            "inbound.answered",
            spec={
                "dial_in": tel.dial_in,
                "dial_ms": dial_ms,
                "participant_identity": getattr(sip_info, "participant_identity", sip_identity),
                "sip_call_id": getattr(sip_info, "sip_call_id", None),
            },
            include_dialogue=False,
        )

        # Resolve agent-room: explicit config/scenario, else discover room with agent.
        agent_room_name = tel.agent_room
        if not agent_room_name and tel.agent_room_name_template:
            agent_room_name = tel.agent_room_name_template.replace("{run_id}", ctx.run_id)

        if agent_room_name:
            # Optional explicit dispatch into known room (if agent not auto-dispatched).
            try:
                await adapter.dispatch_agent(agent_room_name, ctx.dispatch_metadata)
            except Exception:
                pass
            agent_identity = await adapter.wait_for_agent(agent_room_name)
        else:
            agent_room_name, agent_identity = await adapter.find_agent_room(
                exclude_rooms={sim_room_name},
                timeout_ms=ctx.cfg.livekit.agent_join_timeout_ms,
            )

        writer.emit(
            "dispatch.agent_joined",
            spec={
                "identity": agent_identity,
                "room": agent_room_name,
                "mode": "inbound_sip",
            },
            include_dialogue=False,
        )
        writer.emit(
            "sip.participant_connected",
            spec={
                "identity": sip_identity,
                "room": sim_room_name,
                "role": "inbound_caller_leg",
            },
            include_dialogue=False,
        )

        agent_room = await adapter.connect_participant(
            agent_room_name,
            identity=f"lk-sim-obs-{ctx.run_id[:8]}",
            name="Agent Simulator Observer",
        )

        # Human side from agent POV = SIP participant in agent-room (caller).
        try:
            human_sip = await adapter.wait_for_sip_participant(
                agent_room_name, timeout_ms=10_000
            )
        except Exception:
            human_sip = sip_identity

        return SimLegHandle(
            agent_room=agent_room,
            sim_room=sim_room,
            agent_room_name=agent_room_name,
            sim_room_name=sim_room_name,
            sim_identity=human_sip,
            agent_identity=agent_identity,
            mode="inbound_sip",
            gemini_listen_identity=None,
            gemini_listen_sip=True,
            rooms_to_delete=[agent_room_name, sim_room_name],
            meta={
                "dial_ms": dial_ms,
                "dial_in": tel.dial_in,
                "sip_call_id": getattr(sip_info, "sip_call_id", None),
            },
        )


# ── Pattern B: agent_dials (optional) ────────────────────────────────────────


class AgentDialsSimLeg:
    """Dispatch only; wait for SIP participant the agent creates (cooperative agent)."""

    async def connect(self, ctx: SimLegContext) -> SimLegHandle:
        from ..scenario import effective_telephony

        tel = effective_telephony(ctx.scenario, ctx.cfg)
        adapter, writer = ctx.adapter, ctx.writer

        # Optional sim-room if call_to / sim inbound is provisioned for Gemini answer.
        sim_room_name = f"lk-sim-sip-{ctx.run_id}"
        agent_room_name = room_name_for_run(ctx.run_id)
        await adapter.create_room(sim_room_name)
        await adapter.create_room(agent_room_name)

        sim_room = await adapter.connect_participant(
            sim_room_name, identity=SIM_IDENTITY, name="Agent Simulator Caller"
        )
        writer.emit(
            "sim.connected",
            spec={"identity": SIM_IDENTITY, "room": sim_room_name, "mode": "agent_dials"},
            include_dialogue=False,
        )

        dispatch_id = await adapter.dispatch_agent(agent_room_name, ctx.dispatch_metadata)
        agent_identity = await adapter.wait_for_agent(agent_room_name)
        writer.emit(
            "dispatch.agent_joined",
            spec={"identity": agent_identity, "mode": "agent_dials", "dispatch_id": dispatch_id},
            include_dialogue=False,
        )
        writer.emit(
            "outbound.wait_agent_dial",
            spec={"room": agent_room_name, "note": "waiting for agent to create SIP participant"},
            include_dialogue=False,
        )

        sip_id = await adapter.wait_for_sip_participant(
            agent_room_name,
            timeout_ms=max(60_000, ctx.cfg.livekit.agent_join_timeout_ms),
            require_active=True,
        )
        writer.emit(
            "sip.participant_connected",
            spec={"identity": sip_id, "room": agent_room_name, "role": "agent_dials"},
            include_dialogue=False,
        )
        writer.emit(
            "outbound.dial_answered",
            spec={"participant_identity": sip_id, "mode": "agent_dials"},
            include_dialogue=False,
        )

        agent_room = await adapter.connect_participant(
            agent_room_name,
            identity=f"lk-sim-obs-{ctx.run_id[:8]}",
            name="Agent Simulator Observer",
        )
        return SimLegHandle(
            agent_room=agent_room,
            sim_room=sim_room,
            agent_room_name=agent_room_name,
            sim_room_name=sim_room_name,
            sim_identity=sip_id,
            agent_identity=agent_identity,
            mode="agent_dials",
            gemini_listen_sip=True,
            rooms_to_delete=[agent_room_name, sim_room_name],
            meta={"dispatch_id": dispatch_id, "call_to": tel.call_to},
        )


def _sip_error_spec(exc: BaseException, *, call_to: str) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "call_to": call_to,
        "error": f"{type(exc).__name__}: {exc}",
    }
    meta = getattr(exc, "metadata", None)
    if isinstance(meta, dict):
        if meta.get("sip_status_code") is not None:
            spec["sip_status_code"] = meta.get("sip_status_code")
        if meta.get("sip_status") is not None:
            spec["sip_status"] = meta.get("sip_status")
    code = getattr(exc, "sip_status_code", None)
    if code is not None:
        spec["sip_status_code"] = code
    status = getattr(exc, "sip_status", None)
    if status is not None:
        spec["sip_status"] = status
    return spec
