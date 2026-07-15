"""P1.H1 — people-pleaser counter templates parse + compile."""

from __future__ import annotations

from pathlib import Path

from livekit_agent_simulator.scenario import parse_scenario

ROOT = Path(__file__).resolve().parents[1] / "templates" / "examples"


def test_refuse_card_template():
    s = parse_scenario(ROOT / "people-pleaser-refuse-card.jsonl")
    assert any(st.barge_in for st in s.script_steps)
    assert any(st.barge_in and "card" in (st.say or "").lower() for st in s.script_steps)
    assert s.asserts and s.asserts.transcript and s.asserts.transcript[0].must_not_match


def test_hangup_threat_template():
    s = parse_scenario(ROOT / "people-pleaser-hangup-threat.jsonl")
    assert any(st.action == "hang_up" for st in s.script_steps)
    assert s.asserts and any(o.type == "ended_by" for o in s.asserts.outcomes)
