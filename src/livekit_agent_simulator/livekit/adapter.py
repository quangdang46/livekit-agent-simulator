"""LiveKit server-side plumbing: room create, agent dispatch, SIP dial, token, connect.

Pattern (WebRTC): create room → dispatch by `agent_name` → poll until agent joins.
Pattern (SIP): create_sip_participant via livekit-api SipService.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from livekit import api, rtc

from ..config import SimConfig

SIM_IDENTITY = "lk-sim-caller"
SIM_NAME = "Agent Simulator Caller"


class AgentJoinTimeout(Exception):
    pass


class SipParticipantTimeout(Exception):
    pass


@dataclass
class DispatchResult:
    room_name: str
    agent_identity: str
    dispatch_id: str | None


def room_name_for_run(run_id: str) -> str:
    return f"lk-sim-{run_id}"


class LiveKitAdapter:
    def __init__(self, cfg: SimConfig) -> None:
        self.cfg = cfg
        self._lkapi: api.LiveKitAPI | None = None

    async def __aenter__(self) -> "LiveKitAdapter":
        self._lkapi = api.LiveKitAPI(
            self.cfg.livekit.url, self.cfg.livekit.api_key, self.cfg.livekit.api_secret
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._lkapi is not None:
            await self._lkapi.aclose()
            self._lkapi = None

    @property
    def lkapi(self) -> api.LiveKitAPI:
        assert self._lkapi is not None, "use `async with LiveKitAdapter(cfg)`"
        return self._lkapi

    # ------------------------------------------------------------- rooms

    async def create_room(self, room_name: str) -> None:
        await self.lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, empty_timeout=300, max_participants=8)
        )
        if self.cfg.livekit.room_prepare_ms > 0:
            await asyncio.sleep(self.cfg.livekit.room_prepare_ms / 1000)

    async def create_room_and_dispatch(
        self, run_id: str, dispatch_metadata: str | None = None
    ) -> DispatchResult:
        room_name = room_name_for_run(run_id)
        await self.create_room(room_name)
        dispatch_id = await self.dispatch_agent(room_name, dispatch_metadata)
        return DispatchResult(
            room_name=room_name,
            agent_identity="",
            dispatch_id=dispatch_id,
        )

    async def dispatch_agent(
        self, room_name: str, dispatch_metadata: str | None = None
    ) -> str | None:
        metadata = (
            dispatch_metadata
            if dispatch_metadata is not None
            else self.cfg.livekit.dispatch_metadata
        )
        dispatch = await self.lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=self.cfg.livekit.agent_name,
                room=room_name,
                metadata=metadata or "",
            )
        )
        return getattr(dispatch, "id", None)

    # ----------------------------------------------------------- wait agent

    async def wait_for_agent(self, room_name: str, poll_ms: int = 500) -> str:
        """Poll participants until an agent participant joins. Returns its identity."""
        deadline = asyncio.get_event_loop().time() + self.cfg.livekit.agent_join_timeout_ms / 1000
        while True:
            res = await self.lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            for p in res.participants:
                if self._is_agent_participant(p):
                    return p.identity
            if asyncio.get_event_loop().time() > deadline:
                raise AgentJoinTimeout(
                    f"Agent `{self.cfg.livekit.agent_name}` did not join room `{room_name}` within "
                    f"{self.cfg.livekit.agent_join_timeout_ms}ms. Is the agent process running and "
                    f"registered with that exact agent_name?"
                )
            await asyncio.sleep(poll_ms / 1000)

    async def find_agent_room(
        self,
        *,
        exclude_rooms: set[str] | None = None,
        timeout_ms: int | None = None,
        poll_ms: int = 500,
    ) -> tuple[str, str]:
        """Discover a room that currently has the configured agent. Returns (room, identity)."""
        exclude = exclude_rooms or set()
        timeout = timeout_ms if timeout_ms is not None else self.cfg.livekit.agent_join_timeout_ms
        deadline = asyncio.get_event_loop().time() + timeout / 1000
        while True:
            rooms = await self.lkapi.room.list_rooms(api.ListRoomsRequest())
            for room in rooms.rooms:
                name = getattr(room, "name", "") or ""
                if not name or name in exclude:
                    continue
                res = await self.lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=name)
                )
                for p in res.participants:
                    if self._is_agent_participant(p):
                        return name, p.identity
            if asyncio.get_event_loop().time() > deadline:
                raise AgentJoinTimeout(
                    f"No room with agent `{self.cfg.livekit.agent_name}` found within {timeout}ms. "
                    f"For inbound_sip, set Telephony.agent_room or telephony.agent_room_name_template "
                    f"if the dispatch rule room name is known."
                )
            await asyncio.sleep(poll_ms / 1000)

    @staticmethod
    def _is_agent_participant(p: object) -> bool:
        kind = getattr(p, "kind", None)
        kind_name = ""
        try:
            from livekit.protocol.models import ParticipantInfo

            kind_name = ParticipantInfo.Kind.Name(kind) if kind is not None else ""
        except Exception:
            pass
        identity = getattr(p, "identity", "") or ""
        return kind_name == "AGENT" or identity.startswith("agent-")

    @staticmethod
    def _is_sip_participant(p: object) -> bool:
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
        return False

    @staticmethod
    def _sip_call_status(p: object) -> str | None:
        attrs = getattr(p, "attributes", None) or {}
        if isinstance(attrs, dict):
            return attrs.get("sip.callStatus") or attrs.get("sip.call_status")
        return None

    async def wait_for_sip_participant(
        self,
        room_name: str,
        *,
        timeout_ms: int = 30_000,
        poll_ms: int = 400,
        require_active: bool = False,
        identity_prefix: str | None = None,
    ) -> str:
        """Poll until a SIP participant is present (optionally sip.callStatus=active)."""
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while True:
            res = await self.lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            for p in res.participants:
                if not self._is_sip_participant(p):
                    continue
                identity = getattr(p, "identity", "") or ""
                if identity_prefix and not identity.startswith(identity_prefix):
                    continue
                if require_active:
                    status = self._sip_call_status(p)
                    if status and status != "active":
                        continue
                return identity
            if asyncio.get_event_loop().time() > deadline:
                raise SipParticipantTimeout(
                    f"No SIP participant in room `{room_name}` within {timeout_ms}ms"
                    + (" with sip.callStatus=active" if require_active else "")
                )
            await asyncio.sleep(poll_ms / 1000)

    # -------------------------------------------------------------- SIP

    async def create_sip_participant(
        self,
        *,
        room_name: str,
        sip_trunk_id: str,
        sip_call_to: str,
        participant_identity: str,
        participant_name: str = "SIP",
        wait_until_answered: bool = True,
        krisp_enabled: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Create an outbound SIP participant (livekit-api 1.1.1+)."""
        req = api.CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=sip_call_to,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_name=participant_name,
            wait_until_answered=wait_until_answered,
            krisp_enabled=krisp_enabled,
        )
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return await self.lkapi.sip.create_sip_participant(req, **kwargs)

    # -------------------------------------------------------------- connect

    def build_token(
        self,
        room_name: str,
        *,
        identity: str = SIM_IDENTITY,
        name: str = SIM_NAME,
    ) -> str:
        return (
            api.AccessToken(self.cfg.livekit.api_key, self.cfg.livekit.api_secret)
            .with_identity(identity)
            .with_name(name)
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )

    async def connect_simulator(self, room_name: str) -> rtc.Room:
        return await self.connect_participant(room_name, identity=SIM_IDENTITY, name=SIM_NAME)

    async def connect_participant(
        self,
        room_name: str,
        *,
        identity: str,
        name: str,
    ) -> rtc.Room:
        room = rtc.Room()
        token = self.build_token(room_name, identity=identity, name=name)
        await room.connect(
            self.cfg.livekit.url,
            token,
            options=rtc.RoomOptions(auto_subscribe=True),
        )
        return room

    # -------------------------------------------------------------- cleanup

    async def delete_room(self, room_name: str) -> None:
        try:
            await self.lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass
