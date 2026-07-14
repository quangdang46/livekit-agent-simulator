"""P1.H — constraint_respected outcome (hard must_not on caller transcript)."""

from __future__ import annotations

import pytest

from livekit_agent_simulator.asserts import evaluate_asserts, parse_assert_spec


def _user_final(text: str, ms: int = 1) -> dict:
    return {
        "kind": "transcript.user.final",
        "ts_mono_ms": ms,
        "spec": {"text": text},
    }


def test_parse_constraint_respected():
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "no_card",
                    "type": "constraint_respected",
                    "must_not_phrases": ["4111", "card number"],
                }
            ]
        }
    )
    oc = spec.outcomes[0]
    assert oc.type == "constraint_respected"
    assert "4111" in oc.must_not_phrases


def test_parse_requires_something():
    with pytest.raises(ValueError, match="constraint_respected"):
        parse_assert_spec(
            {"outcomes": [{"id": "x", "type": "constraint_respected"}]}
        )


def test_pass_when_clean():
    events = [_user_final("I need help with my order please")]
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "no_card",
                    "type": "constraint_respected",
                    "must_not_phrases": ["4111", "ssn"],
                }
            ]
        }
    )
    out = evaluate_asserts(events, spec)
    assert out["pass"] is True


def test_fail_when_phrase_leaked():
    events = [_user_final("Sure my card is 4111111111111111")]
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "no_card",
                    "type": "constraint_respected",
                    "must_not_phrases": ["4111"],
                }
            ]
        }
    )
    out = evaluate_asserts(events, spec)
    assert out["pass"] is False
    check = next(c for c in out["checks"] if c["type"] == "constraint_respected")
    assert check["violations"]


def test_regex_forbidden():
    events = [_user_final("my pin is 1234")]
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "no_pin",
                    "type": "constraint_respected",
                    "must_not_match": r"\b\d{4}\b",
                }
            ]
        }
    )
    out = evaluate_asserts(events, spec)
    assert out["pass"] is False


def test_prompt_only_pending_judge():
    events = [_user_final("hello")]
    spec = parse_assert_spec(
        {
            "outcomes": [
                {
                    "id": "policy",
                    "type": "constraint_respected",
                    "prompt": "Caller never shared payment details",
                }
            ]
        }
    )
    out = evaluate_asserts(events, spec)
    assert out["pass"] is True
    assert out.get("pending_llm_outcomes")
    assert any(c.get("pending_judge") for c in out["checks"])
