"""Dispatch + wait-agent logic against a mocked LiveKitAPI (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from livekit_agent_simulator.config import (
    LiveKitConfig,
    ObserveConfig,
    SimConfig,
    SimulatorConfig,
    SimulatorVoiceConfig,
)
from livekit_agent_simulator.livekit.adapter import (
    AgentJoinTimeout,
    LiveKitAdapter,
    room_name_for_run,
)


def make_cfg(tmp_path, join_timeout_ms=800):
    return SimConfig(
        project_root=tmp_path,
        livekit=LiveKitConfig(
            url="wss://demo.livekit.cloud",
            api_key="APIkey",
            api_secret="secret",
            agent_name="my-agent-local",
            room_prepare_ms=0,
            agent_join_timeout_ms=join_timeout_ms,
        ),
        simulator=SimulatorConfig(google_api_key="AIzaTest", voice=SimulatorVoiceConfig()),
        observe=ObserveConfig(),
    )


def make_adapter(cfg, participants_batches):
    """Adapter with a fake lkapi; list_participants returns successive batches."""
    adapter = LiveKitAdapter(cfg)
    lkapi = MagicMock()
    lkapi.room.create_room = AsyncMock()
    lkapi.agent_dispatch.create_dispatch = AsyncMock(return_value=SimpleNamespace(id="disp-1"))

    batches = list(participants_batches)

    async def list_participants(_req):
        batch = batches.pop(0) if len(batches) > 1 else batches[0]
        return SimpleNamespace(participants=batch)

    lkapi.room.list_participants = AsyncMock(side_effect=list_participants)
    adapter._lkapi = lkapi
    return adapter, lkapi


def agent_participant(identity="agent-AJ_xyz"):
    return SimpleNamespace(identity=identity, kind=4)  # ParticipantInfo.Kind.AGENT == 4


def test_room_name_convention():
    assert room_name_for_run("r-20260710-101500-ab12") == "lk-sim-r-20260710-101500-ab12"


async def test_create_room_and_dispatch(tmp_path):
    cfg = make_cfg(tmp_path)
    adapter, lkapi = make_adapter(cfg, [[]])

    result = await adapter.create_room_and_dispatch("r-test-1")

    assert result.room_name == "lk-sim-r-test-1"
    assert result.dispatch_id == "disp-1"
    create_req = lkapi.room.create_room.call_args.args[0]
    assert create_req.name == "lk-sim-r-test-1"
    dispatch_req = lkapi.agent_dispatch.create_dispatch.call_args.args[0]
    assert dispatch_req.agent_name == "my-agent-local"
    assert dispatch_req.room == "lk-sim-r-test-1"


async def test_wait_for_agent_joins_on_second_poll(tmp_path):
    cfg = make_cfg(tmp_path)
    adapter, _ = make_adapter(cfg, [[], [agent_participant()]])

    identity = await adapter.wait_for_agent("lk-sim-r-test-1", poll_ms=10)
    assert identity == "agent-AJ_xyz"


async def test_wait_for_agent_ignores_non_agent_participants(tmp_path):
    cfg = make_cfg(tmp_path)
    human = SimpleNamespace(identity="lk-sim-caller", kind=0)
    adapter, _ = make_adapter(cfg, [[human], [human, agent_participant("agent-worker")]])

    identity = await adapter.wait_for_agent("room", poll_ms=10)
    assert identity == "agent-worker"


async def test_wait_for_agent_timeout(tmp_path):
    cfg = make_cfg(tmp_path, join_timeout_ms=100)
    adapter, _ = make_adapter(cfg, [[]])

    with pytest.raises(AgentJoinTimeout, match="my-agent-local"):
        await adapter.wait_for_agent("room", poll_ms=20)


def test_build_token_contains_room_grant(tmp_path):
    cfg = make_cfg(tmp_path)
    adapter = LiveKitAdapter(cfg)
    token = adapter.build_token("lk-sim-r-1")
    assert isinstance(token, str) and token.count(".") == 2  # JWT shape


async def test_create_sip_participant_request(tmp_path):
    cfg = make_cfg(tmp_path)
    adapter, lkapi = make_adapter(cfg, [[]])
    lkapi.sip = MagicMock()
    lkapi.sip.create_sip_participant = AsyncMock(
        return_value=SimpleNamespace(
            participant_identity="sip-1",
            sip_call_id="SC_1",
            room_name="room",
            participant_id="PA_1",
        )
    )
    info = await adapter.create_sip_participant(
        room_name="room-a",
        sip_trunk_id="ST_x",
        sip_call_to="+1555",
        participant_identity="sip-1",
        wait_until_answered=True,
        krisp_enabled=False,
    )
    assert info.participant_identity == "sip-1"
    req = lkapi.sip.create_sip_participant.call_args.args[0]
    assert req.sip_trunk_id == "ST_x"
    assert req.sip_call_to == "+1555"
    assert req.room_name == "room-a"
    assert req.wait_until_answered is True


async def test_wait_for_sip_participant(tmp_path):
    cfg = make_cfg(tmp_path)
    sip_p = SimpleNamespace(
        identity="sip-leg",
        kind=3,
        attributes={"sip.callStatus": "active"},
    )
    adapter, _ = make_adapter(cfg, [[], [sip_p]])
    identity = await adapter.wait_for_sip_participant("room", timeout_ms=500, poll_ms=10)
    assert identity == "sip-leg"
