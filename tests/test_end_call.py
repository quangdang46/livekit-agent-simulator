from livekit_agent_simulator.gemini.end_call import (
    END_CALL_TOKEN,
    contains_end_call_signal,
    strip_end_call_signal,
)


def test_token_detected():
    assert contains_end_call_signal(f"Bye. {END_CALL_TOKEN}")
    assert strip_end_call_signal(f"Bye. {END_CALL_TOKEN}") == "Bye."


def test_spoken_end_call_detected_and_stripped():
    assert contains_end_call_signal("Cảm ơn nha. Tạm biệt. End call.")
    assert strip_end_call_signal("Cảm ơn nha. Tạm biệt. End call.") == "Cảm ơn nha. Tạm biệt."


def test_spoken_hang_up_variants():
    assert contains_end_call_signal("Thanks, hang up")
    assert contains_end_call_signal("ok END_CALL thanks")
    assert strip_end_call_signal("Thanks, hang-up now") == "Thanks, now"


def test_no_false_positive_on_normal_speech():
    text = "I want to call next Friday about the end of the billing period."
    assert not contains_end_call_signal(text)
    assert strip_end_call_signal(text) == text
