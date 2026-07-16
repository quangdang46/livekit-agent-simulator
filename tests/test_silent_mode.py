"""Coval Silent Mode: unresponsive / dead-air caller."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from livekit_agent_simulator.behavior_compile import (
    apply_caller_behavior,
    compile_from_speech_conditions,
    silent_mode_enabled,
)
from livekit_agent_simulator.caller.policy import CallerPolicyContext
from livekit_agent_simulator.caller.prompt_sections import (
    FirstSpeakerSection,
    SpeechConditionsSection,
)
from livekit_agent_simulator.script import ScriptStep


def test_silent_mode_enabled_truthy() -> None:
    assert silent_mode_enabled({"speech_conditions": {"silent_mode": True}})
    assert silent_mode_enabled({"speech_conditions": {"silent_mode": "on"}})
    assert silent_mode_enabled({"speech_conditions": {"silent": "yes"}})
    assert not silent_mode_enabled({"speech_conditions": {"silent_mode": False}})
    assert not silent_mode_enabled({})


def test_compile_skips_auto_steps_when_silent() -> None:
    persona = {
        "speech_conditions": {
            "silent_mode": True,
            "noise": "builtin:noise.ambient",
            "noise_when": "background",
            "barge_policy": "mid_agent_turn",
            "silence_ms": 5000,
        }
    }
    assert compile_from_speech_conditions(persona) == []


def test_apply_caller_behavior_drops_speak_and_barge() -> None:
    persona = {"speech_conditions": {"silent_mode": True}}
    explicit = [
        ScriptStep(id="hi", trigger="time", delay_ms=100, say="hello", action="speak"),
        ScriptStep(
            id="cut",
            trigger="agent_speaking",
            delay_ms=200,
            say="wait",
            barge_in=True,
            action="speak",
        ),
        ScriptStep(id="bye", trigger="time", delay_ms=5000, say="bye", action="hang_up"),
    ]
    steps, _ = apply_caller_behavior(
        persona,
        {"barge_ins": [{"id": "b1", "say": "Hey", "after_agent_ms": 400}]},
        explicit,
        None,
    )
    assert all(s.action in ("wait", "hang_up") for s in steps)
    hang = next(s for s in steps if s.action == "hang_up")
    assert hang.say == ""
    assert not any(s.barge_in for s in steps)


def test_prompt_silent_mode_hard() -> None:
    ctx = CallerPolicyContext(
        persona={"speech_conditions": {"silent_mode": True}},
        locale="en-US",
        context={},
        script_steps=[],
        first_speaker="agent",
    )
    sc = " ".join(SpeechConditionsSection().render(ctx))
    assert "SILENT MODE" in sc
    fs = " ".join(FirstSpeakerSection().render(ctx))
    assert "zero speech" in fs or "mute" in fs


@pytest.mark.asyncio
async def test_bridge_blocks_freestyle_and_inject() -> None:
    from livekit_agent_simulator.gemini.live_session import GeminiCallerBridge

    events: list[tuple] = []

    class W:
        def emit(self, kind, spec=None, **kw):
            events.append((kind, spec))

    bridge = object.__new__(GeminiCallerBridge)
    bridge._silent_mode = True
    bridge._script_hangup_farewell = False
    bridge._inject_turn_active = False
    bridge._mute_persona_audio = False
    bridge.writer = W()
    bridge._suppress_output_until_mono = None

    assert bridge._allow_persona_room_audio() is False

    await bridge.inject_cue("hello", label="x")
    assert any(k == "sim.silent_mode_skip_inject" for k, _ in events)

    # hang-up farewell path still allowed through allow()
    bridge._script_hangup_farewell = True
    assert bridge._allow_persona_room_audio() is True


@pytest.mark.asyncio
async def test_nudge_skipped_in_silent_mode() -> None:
    from livekit_agent_simulator.caller_nudge import nudge_caller_after_agent_greeting

    events: list = []

    class W:
        def emit(self, kind, spec=None, **kw):
            events.append(kind)

    bridge = SimpleNamespace(end_call=asyncio.Event(), _silent_mode=True)
    observer = SimpleNamespace(agent_has_spoken=True, user_has_spoken=False)
    await nudge_caller_after_agent_greeting(
        observer, bridge, W(), first_speaker="agent", silent_mode=True
    )
    assert "sim.agent_greeted_nudge_skipped" in events
    assert "sim.agent_greeted_nudge" not in events
