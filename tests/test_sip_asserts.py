"""SIP assert kinds."""

from livekit_agent_simulator.asserts import evaluate_asserts, parse_assert_spec


def test_parse_sip_assert():
    spec = parse_assert_spec(
        {
            "sip": {
                "participant_present": True,
                "dial_answered": True,
                "call_status_any": ["active"],
            }
        }
    )
    assert spec.sip is not None
    assert spec.sip.participant_present is True
    assert spec.sip.dial_answered is True
    assert spec.sip.call_status_any == ("active",)
    assert not spec.empty


def test_eval_sip_pass():
    events = [
        {"kind": "outbound.dial_started", "spec": {}},
        {"kind": "sip.participant_connected", "spec": {"identity": "sip-1"}},
        {"kind": "outbound.dial_answered", "spec": {"dial_ms": 1200}},
        {"kind": "sip.call_status", "spec": {"status": "active"}},
    ]
    asserts = parse_assert_spec(
        {"sip": {"participant_present": True, "dial_answered": True, "call_status_any": ["active"]}}
    )
    result = evaluate_asserts(events, asserts)
    assert result["pass"] is True
    kinds = {c["check"] for c in result["checks"]}
    assert "sip_participant_present" in kinds
    assert "sip_dial_answered" in kinds
    assert "sip_call_status" in kinds


def test_eval_sip_fail_missing():
    events = [{"kind": "run.started", "spec": {}}]
    asserts = parse_assert_spec({"sip": {"participant_present": True, "dial_answered": True}})
    result = evaluate_asserts(events, asserts)
    assert result["pass"] is False
