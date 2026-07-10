"""LiveKit server-side plumbing: room create, explicit agent dispatch, token, connect.

Pattern: create room → dispatch by `agent_name` → poll until agent participant joins.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from livekit import api, rtc

from ..config import SimConfig

SIM_IDENTITY = "lk-sim-caller"
SIM_NAME = "Agent Simulator Caller"


class AgentJoinTimeout(Exception):
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

    # ------------------------------------------------------------- dispatch

    async def create_room_and_dispatch(
        self, run_id: str, dispatch_metadata: str | None = None
    ) -> DispatchResult:
        room_name = room_name_for_run(run_id)
        await self.lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, empty_timeout=300, max_participants=8)
        )
        if self.cfg.livekit.room_prepare_ms > 0:
            await asyncio.sleep(self.cfg.livekit.room_prepare_ms / 1000)

        metadata = dispatch_metadata if dispatch_metadata is not None else self.cfg.livekit.dispatch_metadata

        dispatch = await self.lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=self.cfg.livekit.agent_name,
                room=room_name,
                metadata=metadata or "",
            )
        )
        return DispatchResult(
            room_name=room_name,
            agent_identity="",  # filled by wait_for_agent
            dispatch_id=getattr(dispatch, "id", None),
        )

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
                    f"{self.cfg.livekit.agent_join_timeout_ms}ms. Is the worker running and "
                    f"registered with that exact agent_name?"
                )
            await asyncio.sleep(poll_ms / 1000)

    @staticmethod
    def _is_agent_participant(p: object) -> bool:
        kind = getattr(p, "kind", None)
        # ParticipantInfo.Kind.AGENT == 4 in the protocol; compare by name to stay version-safe.
        kind_name = ""
        try:
            from livekit.protocol.models import ParticipantInfo

            kind_name = ParticipantInfo.Kind.Name(kind) if kind is not None else ""
        except Exception:
            pass
        identity = getattr(p, "identity", "") or ""
        return kind_name == "AGENT" or identity.startswith("agent-")

    # -------------------------------------------------------------- connect

    def build_token(self, room_name: str) -> str:
        return (
            api.AccessToken(self.cfg.livekit.api_key, self.cfg.livekit.api_secret)
            .with_identity(SIM_IDENTITY)
            .with_name(SIM_NAME)
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )

    async def connect_simulator(self, room_name: str) -> rtc.Room:
        room = rtc.Room()
        token = self.build_token(room_name)
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
            pass  # room may already be gone; never fail a run on cleanup
