"""Backchannel agent-continued assert tests."""

from __future__ import annotations

from types import SimpleNamespace

from livekit_agent_simulator.asserts import evaluate_asserts, parse_assert_spec


def test_backchannel_continued_parse():
    spec = parse_assert_spec({
        "outcomes": [{"id": "bc1", "type": "backchannel_agent_continued"}]
    })
    assert any(oc.type == "backchannel_agent_continued" for oc in spec.outcomes)


def test_backchannel_continued_skipped_when_no_cues():
    events = [
        {"kind": "transcript.agent.final", "spec": {"text": "hello"}, "ts_mono_ms": 1000},
    ]
    spec = parse_assert_spec({
        "outcomes": [{"id": "bc1", "type": "backchannel_agent_continued"}]
    })
    result = evaluate_asserts(events, spec)
    chk = next(c for c in result["checks"] if c["type"] == "backchannel_agent_continued")
    assert chk.get("skipped")
    assert chk.get("pass") is True


def test_backchannel_continued_agent_keeps_talking():
    events = [
        {"kind": "sim.script.cue", "spec": {"class": "backchannel", "barge_in": False}, "ts_mono_ms": 2000},
        {"kind": "transcript.agent.final", "spec": {"text": "continuing"}, "ts_mono_ms": 3000},
    ]
    spec = parse_assert_spec({
        "outcomes": [{"id": "bc1", "type": "backchannel_agent_continued"}]
    })
    result = evaluate_asserts(events, spec)
    chk = next(c for c in result["checks"] if c["type"] == "backchannel_agent_continued")
    assert chk["pass"] is True
    assert chk["continued"] is True


def test_backchannel_continued_agent_stops():
    events = [
        {"kind": "sim.script.cue", "spec": {"class": "backchannel", "barge_in": False}, "ts_mono_ms": 2000},
        # No agent finals after backchannel
    ]
    spec = parse_assert_spec({
        "outcomes": [{"id": "bc1", "type": "backchannel_agent_continued"}]
    })
    result = evaluate_asserts(events, spec)
    chk = next(c for c in result["checks"] if c["type"] == "backchannel_agent_continued")
    assert chk["pass"] is False
