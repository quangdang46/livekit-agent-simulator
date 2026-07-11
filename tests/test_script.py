import pytest

from livekit_agent_simulator.script_parse import parse_script_steps, parse_script_verify
from livekit_agent_simulator.script_runner import ScriptStep, ScriptVerifySpec, evaluate_script_log


def test_parse_script_steps():
    steps = parse_script_steps(
        {
            "steps": [
                {
                    "id": "bc",
                    "trigger": "agent_speaking",
                    "delay_ms": 700,
                    "say": "うん",
                    "label": "backchannel",
                },
                {
                    "id": "quiet",
                    "trigger": "silence",
                    "delay_ms": 2000,
                    "action": "wait",
                },
                {
                    "id": "bi",
                    "barge_in": True,
                    "say": "wait—",
                },
            ]
        },
        "test.jsonl:1",
    )
    assert len(steps) == 3
    assert steps[0].say == "うん"
    assert steps[0].delay_ms == 700
    assert steps[1].trigger == "silence"
    assert steps[1].action == "wait"
    assert steps[2].trigger == "agent_speaking"
    assert steps[2].delay_ms == 250


def test_parse_script_verify():
    v = parse_script_verify({"min_agent_finals_after_first_cue": 1, "max_interruptions": 0})
    assert v is not None
    assert v.min_agent_finals_after_first_cue == 1
    assert v.max_interruptions == 0


def test_evaluate_script_log_pass_during_agent():
    steps = [ScriptStep("bc", "agent_speaking", 800, "うん", "backchannel")]
    events = [
        {"kind": "sim.script.cue", "ts_mono_ms": 5000, "spec": {"step_id": "bc", "during_agent_speech": True}},
        {"kind": "transcript.agent.final", "ts_mono_ms": 8000, "spec": {"text": "continued"}},
    ]
    result = evaluate_script_log(
        events,
        steps,
        ScriptVerifySpec(min_agent_finals_after_first_cue=1, max_interruptions=0),
    )
    assert result["pass"] is True
    assert result["agent_finals_after_first_cue"] == 1


def test_evaluate_script_log_fail_not_during_agent():
    steps = [ScriptStep("bc", "agent_speaking", 800, "うん", "backchannel")]
    events = [
        {"kind": "sim.script.cue", "ts_mono_ms": 5000, "spec": {"step_id": "bc", "during_agent_speech": False}},
    ]
    result = evaluate_script_log(events, steps, ScriptVerifySpec())
    assert result["pass"] is False


def test_evaluate_script_log_interrupt_scenario():
    steps = [ScriptStep("ri", "agent_speaking", 800, "wait", "interrupt")]
    events = [
        {"kind": "sim.script.cue", "ts_mono_ms": 4000, "spec": {"step_id": "ri", "during_agent_speech": True}},
        {"kind": "interruption", "ts_mono_ms": 4100, "spec": {}},
    ]
    result = evaluate_script_log(events, steps, ScriptVerifySpec(min_interruptions=1))
    assert result["pass"] is True


def test_evaluate_silence_wait_and_agent_resume():
    steps = [
        ScriptStep("q", "silence", 2000, say="", label="quiet", action="wait"),
    ]
    events = [
        {
            "kind": "sim.script.wait",
            "ts_mono_ms": 5000,
            "spec": {"step_id": "q", "trigger": "silence", "action": "wait"},
        },
        {"kind": "transcript.agent.final", "ts_mono_ms": 8000, "spec": {"text": "Are you still there?"}},
    ]
    result = evaluate_script_log(
        events,
        steps,
        ScriptVerifySpec(min_agent_finals_after_silence=1),
    )
    assert result["pass"] is True
    assert result["agent_finals_after_silence"] == 1


def test_build_caller_behavior_summary():
    from livekit_agent_simulator.script_runner import build_caller_behavior_summary

    events = [
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 1000,
            "spec": {
                "barge_in": True,
                "during_agent_speech": True,
                "asset": "builtin:voice.barge_short",
            },
        },
        {
            "kind": "sim.script_inject",
            "ts_mono_ms": 1001,
            "spec": {
                "delivery": "room_pcm",
                "asset": "/pkg/templates/cues/barge_wait_vi.wav",
            },
        },
        {"kind": "interruption", "ts_mono_ms": 1100, "spec": {"by": "sim"}},
        {"kind": "sim.script.wait", "ts_mono_ms": 3000, "spec": {"action": "wait"}},
        {"kind": "transcript.agent.final", "ts_mono_ms": 2500, "spec": {"text": "ok"}},
        {"kind": "transcript.agent.final", "ts_mono_ms": 4000, "spec": {"text": "again"}},
    ]
    s = build_caller_behavior_summary(events)
    assert s["barges_fired"] == 1
    assert s["barges_during_agent"] == 1
    assert s["silences_held"] == 1
    assert s["interruptions"] == 1
    assert s["agent_finals_after_barge"] == 2
    assert s["recovery_ms"] == 1500
    assert "builtin:voice.barge_short" in s["cue_assets"]
    assert any("barge_wait_vi.wav" in a for a in s["cue_assets"])


def test_evaluate_barge_in_recovery():
    steps = [
        ScriptStep(
            "bi",
            "agent_speaking",
            250,
            say="wait—",
            label="barge",
            barge_in=True,
            min_agent_active_ms=200,
        )
    ]
    events = [
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 3000,
            "spec": {
                "step_id": "bi",
                "during_agent_speech": True,
                "barge_in": True,
                "trigger": "agent_speaking",
                "waited_ms": 400,
            },
        },
        {"kind": "transcript.agent.final", "ts_mono_ms": 6000, "spec": {"text": "Sorry, go ahead."}},
    ]
    result = evaluate_script_log(
        events,
        steps,
        ScriptVerifySpec(min_agent_finals_after_barge_in=1),
    )
    assert result["pass"] is True
    assert result["agent_finals_after_barge_in"] == 1
