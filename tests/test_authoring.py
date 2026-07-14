"""P1.G — authoring quality warnings (Hamming persona rubric soft gate)."""

from __future__ import annotations

from types import SimpleNamespace

from livekit_agent_simulator.authoring import (
    authoring_scorecard,
    collect_authoring_warnings,
)
from livekit_agent_simulator.script.models import ScriptStep


def _scenario(**kwargs):
    base = dict(
        persona={"brief": "caller", "goals": ["Ask for status"], "traits": ["polite"]},
        script_steps=[],
        behavior_spec=None,
        script_verify=None,
        asserts=None,
        tags=["smoke"],
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_empty_goals_warns():
    s = _scenario(persona={"brief": "x", "goals": [], "traits": []})
    w = collect_authoring_warnings(s)
    assert any("goals" in x.lower() for x in w)


def test_stress_trait_without_script_warns():
    s = _scenario(persona={"brief": "x", "goals": ["g"], "traits": ["interrupts"]})
    w = collect_authoring_warnings(s)
    assert any("interrupts" in x for x in w)


def test_barge_without_recovery_assert_warns():
    step = ScriptStep(
        id="b1",
        trigger="agent_speaking",
        delay_ms=200,
        say="Wait",
        barge_in=True,
        interrupt_class="correction",
    )
    s = _scenario(script_steps=[step], persona={"brief": "x", "goals": ["g"]})
    w = collect_authoring_warnings(s)
    assert any("recovery" in x.lower() for x in w)


def test_barge_with_recovery_assert_clean():
    step = ScriptStep(
        id="b1",
        trigger="agent_speaking",
        delay_ms=200,
        say="Wait",
        barge_in=True,
        interrupt_class="correction",
    )
    asserts = SimpleNamespace(
        outcomes=[SimpleNamespace(type="recovery", id="r")],
    )
    s = _scenario(
        script_steps=[step],
        asserts=asserts,
        persona={"brief": "x", "goals": ["g"], "traits": ["interrupts"]},
    )
    w = collect_authoring_warnings(s)
    assert not any("recovery" in x.lower() and "no Assert" in x for x in w)


def test_noise_barge_does_not_require_recovery():
    step = ScriptStep(
        id="n1",
        trigger="agent_speaking",
        delay_ms=200,
        say="[noise]",
        barge_in=True,
        interrupt_class="noise",
    )
    s = _scenario(script_steps=[step], persona={"brief": "x", "goals": ["g"]})
    w = collect_authoring_warnings(s)
    assert not any("Recovery barge" in x for x in w)


def test_hang_up_without_ended_by_warns():
    step = ScriptStep(
        id="h1", trigger="time", delay_ms=100, say="bye", action="hang_up"
    )
    s = _scenario(script_steps=[step], persona={"brief": "x", "goals": ["g"]})
    w = collect_authoring_warnings(s)
    assert any("ended_by" in x for x in w)


def test_scorecard_totals():
    s = _scenario(
        persona={
            "brief": "x",
            "goals": ["g"],
            "constraints": ["no card"],
            "traits": ["polite"],
        },
        script_steps=[
            ScriptStep(
                id="b",
                trigger="agent_speaking",
                delay_ms=1,
                say="x",
                barge_in=True,
                interrupt_class="correction",
            )
        ],
        asserts=SimpleNamespace(outcomes=[SimpleNamespace(type="recovery")]),
    )
    sc = authoring_scorecard(s)
    assert sc["total"] >= 6
    assert sc["max"] == 8
