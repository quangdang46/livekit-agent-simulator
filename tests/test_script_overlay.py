"""Tests for Script overlay classification."""

from __future__ import annotations

from livekit_agent_simulator.script.models import ScriptStep, effective_overlay
from livekit_agent_simulator.script_parse import parse_script_steps


def test_effective_overlay_auto_line():
    step = ScriptStep(id="open", trigger="silence", delay_ms=1000, say="Hello")
    assert effective_overlay(step) == "line"


def test_effective_overlay_auto_fixture_barge():
    step = ScriptStep(
        id="b",
        trigger="agent_speaking",
        delay_ms=200,
        say="wait",
        barge_in=True,
        interrupt_class="correction",
    )
    assert effective_overlay(step) == "fixture"


def test_parse_overlay_forced_line_alias():
    steps = parse_script_steps(
        {
            "steps": [
                {
                    "id": "open",
                    "trigger": "silence",
                    "delay_ms": 500,
                    "say": "Hi",
                    "overlay": "forced_line",
                    "require_agent_spoke_first": False,
                }
            ]
        },
        "test.jsonl",
    )
    assert steps[0].overlay == "line"
    assert effective_overlay(steps[0]) == "line"
