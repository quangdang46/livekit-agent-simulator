"""SimLeg factory + WebRTC leg smoke (mocked adapter)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from livekit_agent_simulator.config import (
    LiveKitConfig,
    ObserveConfig,
    SimConfig,
    SimulatorConfig,
    SimulatorVoiceConfig,
    TelephonyConfig,
)
from livekit_agent_simulator.livekit.sim_leg import (
    SimLegContext,
    SimLegError,
    WebRtcSimLeg,
    sim_leg_factory,
)
from livekit_agent_simulator.scenario import CallerSpec, Scenario, TelephonySpec


def make_cfg(tmp_path, **tel):
    return SimConfig(
        project_root=tmp_path,
        livekit=LiveKitConfig(
            url="wss://demo.livekit.cloud",
            api_key="APIkey",
            api_secret="secret",
            agent_name="my-agent",
            room_prepare_ms=0,
            agent_join_timeout_ms=800,
        ),
        simulator=SimulatorConfig(google_api_key="AIzaTest", voice=SimulatorVoiceConfig()),
        observe=ObserveConfig(),
        telephony=TelephonyConfig(**tel) if tel else TelephonyConfig(),
    )


def test_factory_modes():
    assert type(sim_leg_factory("webrtc_sim")).__name__ == "WebRtcSimLeg"
    assert type(sim_leg_factory("outbound_sip")).__name__ == "OutboundSipSimLeg"
    assert type(sim_leg_factory("inbound_sip")).__name__ == "InboundSipSimLeg"
    assert type(sim_leg_factory("agent_dials")).__name__ == "AgentDialsSimLeg"
    with pytest.raises(SimLegError, match="Unknown"):
        sim_leg_factory("fax")


async def test_webrtc_leg_connect(tmp_path):
    cfg = make_cfg(tmp_path)
    scenario = Scenario(
        id="s",
        path=tmp_path / "s.jsonl",
        persona={"brief": "x"},
    )
    writer = MagicMock()
    writer.emit = MagicMock()

    room = MagicMock(name="room")
    adapter = MagicMock()
    adapter.create_room_and_dispatch = AsyncMock(
        return_value=SimpleNamespace(room_name="lk-sim-r1", dispatch_id="d1", agent_identity="")
    )
    adapter.wait_for_agent = AsyncMock(return_value="agent-xyz")
    adapter.connect_simulator = AsyncMock(return_value=room)

    handle = await WebRtcSimLeg().connect(
        SimLegContext(
            adapter=adapter,
            cfg=cfg,
            scenario=scenario,
            writer=writer,
            run_id="r1",
            dispatch_metadata=None,
            first_speaker="agent",
        )
    )
    assert handle.mode == "webrtc_sim"
    assert handle.agent_room is room
    assert handle.sim_room is room
    assert handle.agent_identity == "agent-xyz"
    assert handle.sim_identity == "lk-sim-caller"
    assert handle.rooms_to_delete == ["lk-sim-r1"]
