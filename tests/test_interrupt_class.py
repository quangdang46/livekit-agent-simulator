"""P1.F — Hamming interrupt classes (correction / backchannel / noise)."""

from __future__ import annotations

import pytest

from livekit_agent_simulator.asserts import evaluate_asserts, parse_assert_spec
from livekit_agent_simulator.behavior_compile import compile_from_behavior_spec, compile_from_speech_conditions
from livekit_agent_simulator.metrics import compute_voice_metrics
from livekit_agent_simulator.script.models import (
    RECOVERY_BARGE_CLASSES,
    counts_for_recovery_barge,
    normalize_interrupt_class,
)
from livekit_agent_simulator.script.summary import build_caller_behavior_summary
from livekit_agent_simulator.script_parse import parse_script_steps


def test_normalize_defaults_and_aliases():
    assert normalize_interrupt_class(None, barge_in=True) == "correction"
    assert normalize_interrupt_class(None, barge_in=False) is None
    assert normalize_interrupt_class("uh-huh") == "backchannel"
    assert normalize_interrupt_class("false_positive") == "noise"
    with pytest.raises(ValueError):
        normalize_interrupt_class("not_a_class")


def test_counts_for_recovery():
    assert counts_for_recovery_barge(barge_in=True, interrupt_class="correction")
    assert counts_for_recovery_barge(barge_in=True, interrupt_class="escalate")
    assert not counts_for_recovery_barge(barge_in=True, interrupt_class="noise")
    assert not counts_for_recovery_barge(barge_in=True, interrupt_class="backchannel")
    assert not counts_for_recovery_barge(barge_in=False, interrupt_class="correction")
    assert "correction" in RECOVERY_BARGE_CLASSES


def test_parse_script_step_class():
    steps = parse_script_steps(
        {
            "steps": [
                {
                    "id": "c1",
                    "trigger": "agent_speaking",
                    "say": "No Friday",
                    "barge_in": True,
                    "class": "correction",
                },
                {
                    "id": "bc1",
                    "trigger": "agent_speaking",
                    "say": "uh-huh",
                    "delivery": "room_pcm",
                    "asset": "builtin:voice.backchannel",
                    "barge_in": False,
                    "class": "backchannel",
                },
            ]
        },
        "t",
    )
    assert steps[0].interrupt_class == "correction"
    assert steps[0].barge_in is True
    assert steps[1].interrupt_class == "backchannel"
    assert steps[1].barge_in is False


def test_behavior_backchannel_and_false_interrupt():
    steps = compile_from_behavior_spec(
        {
            "barge_ins": [{"id": "cut", "say": "Wait", "after_agent_ms": 400, "class": "correction"}],
            "backchannels": [{"id": "uh", "after_agent_ms": 900}],
            "false_interrupts": [{"id": "clk", "after_agent_ms": 300}],
        }
    )
    by_id = {s.id: s for s in steps}
    assert by_id["cut"].interrupt_class == "correction" and by_id["cut"].barge_in
    assert by_id["uh"].interrupt_class == "backchannel" and not by_id["uh"].barge_in
    assert by_id["clk"].interrupt_class == "noise" and by_id["clk"].barge_in


def test_speech_conditions_default_correction_class():
    steps = compile_from_speech_conditions(
        {"speech_conditions": {"barge_policy": "mid_agent_turn", "barge_say": "Hold on"}}
    )
    barge = next(s for s in steps if s.barge_in)
    assert barge.interrupt_class == "correction"


def test_metrics_ignore_noise_and_backchannel():
    events = [
        {"kind": "sim.script.cue", "ts_mono_ms": 1000, "spec": {"barge_in": True, "class": "noise"}},
        {"kind": "sim.script.cue", "ts_mono_ms": 2000, "spec": {"barge_in": False, "class": "backchannel"}},
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 3000,
            "spec": {"barge_in": True, "class": "correction"},
        },
        {"kind": "transcript.agent.final", "ts_mono_ms": 3500, "spec": {"text": "ok"}},
    ]
    m = compute_voice_metrics(events)
    assert m.get("barge_count") == 1
    assert m.get("barge_recovery_rate") == 1.0


def test_assert_recovery_uses_correction_only():
    events = [
        {"kind": "sim.script.cue", "ts_mono_ms": 1000, "spec": {"barge_in": True, "class": "noise"}},
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 2000,
            "spec": {"barge_in": True, "class": "correction"},
        },
        {"kind": "transcript.agent.final", "ts_mono_ms": 2500, "spec": {"text": "sorry"}},
    ]
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "rec",
                    "type": "recovery",
                    "min_agent_finals_after_barge_in": 1,
                }
            ]
        }
    )
    out = evaluate_asserts(events, spec)
    assert out["pass"] is True


def test_behavior_summary_by_class():
    events = [
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 1,
            "spec": {"barge_in": True, "class": "correction", "during_agent_speech": True},
        },
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 2,
            "spec": {"barge_in": False, "class": "backchannel"},
        },
        {
            "kind": "sim.script.cue",
            "ts_mono_ms": 3,
            "spec": {"barge_in": True, "class": "noise"},
        },
    ]
    s = build_caller_behavior_summary(events)
    assert s["barges_fired"] == 1  # correction only
    assert s["by_class"]["correction"] == 1
    assert s["by_class"]["backchannel"] == 1
    assert s["by_class"]["noise"] == 1
