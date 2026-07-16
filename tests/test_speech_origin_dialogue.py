"""Tests for freestyle vs script_cue speech_origin classification."""

from __future__ import annotations

from livekit_agent_simulator.web.speech_origin import (
    _mostly_script_say,
    _tag_cues_with_markers,
)


def test_mostly_script_say_exact():
    assert _mostly_script_say(
        "Hi, I'd like to sign up for the service.",
        "Hi, I'd like to sign up for the service.",
    )


def test_mostly_script_say_rejects_bootstrap_concat():
    text = (
        "Hi, I'm Alex. I was looking into the different plans you offer and wanted to ask... "
        "Hi there, I'd like to sign up for your service today."
    )
    say = "Hi there — I'd like to sign up for your service today."
    assert not _mostly_script_say(text, say)


def test_mostly_script_say_rejects_paraphrase_then_open():
    text = "I'd like to sign up for that service. Hi, I'd like to sign up for the service."
    say = "Hi, I'd like to sign up for the service."
    assert not _mostly_script_say(text, say)


def test_tag_concat_stays_natural():
    cues = [
        {
            "role": "user",
            "start_ms": 15000,
            "end_ms": 18000,
            "final_ms": 17500,
            "text": (
                "Hi, I'm Alex. I was looking into the different plans you offer "
                "and wanted to ask... Hi there, I'd like to sign up for your service today."
            ),
        }
    ]
    markers = [
        {
            "type": "script_cue",
            "start_ms": 12000,
            "say": "Hi there — I'd like to sign up for your service today.",
            "step_id": "open",
            "label": "open",
            "barge_in": False,
        }
    ]
    _tag_cues_with_markers(cues, markers)
    assert cues[0]["speech_origin"] == "natural"


def test_tag_exact_open_is_script_cue():
    cues = [
        {
            "role": "user",
            "start_ms": 15000,
            "end_ms": 17000,
            "final_ms": 16500,
            "text": "Hi there — I'd like to sign up for your service today.",
        }
    ]
    markers = [
        {
            "type": "script_cue",
            "start_ms": 12000,
            "say": "Hi there — I'd like to sign up for your service today.",
            "step_id": "open",
            "label": "open",
            "barge_in": False,
        }
    ]
    _tag_cues_with_markers(cues, markers)
    assert cues[0]["speech_origin"] == "script_cue"
    assert cues[0].get("script_step_id") == "open"
