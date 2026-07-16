from livekit_agent_simulator.gemini.end_call import (
    END_CALL_TOKEN,
    contains_end_call_signal,
    contains_farewell_signal,
    should_end_call_on_turn,
    strip_end_call_signal,
    strip_farewell_signal,
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


def test_farewell_soft_bye_detected():
    assert contains_farewell_signal("Okay, thanks. Bye.")
    assert contains_farewell_signal("Goodbye!")
    assert contains_farewell_signal("Alright, see you later")
    assert not contains_farewell_signal("What's the monthly fee?")
    assert strip_farewell_signal("Okay, thanks. Bye.") == "Okay, thanks."


def test_farewell_includes_harness_token():
    assert contains_farewell_signal(f"Thanks {END_CALL_TOKEN}")


def test_dialogue_soft_bye_ends_call():
    assert should_end_call_on_turn(
        pending_script=False,
        ended=False,
        farewell=True,
        scripted_farewell=False,
    )


def test_script_pending_defers_soft_bye():
    assert not should_end_call_on_turn(
        pending_script=True,
        ended=False,
        farewell=True,
        scripted_farewell=False,
    )


def test_scripted_farewell_not_ended_by_bridge_helper():
    assert not should_end_call_on_turn(
        pending_script=False,
        ended=True,
        farewell=True,
        scripted_farewell=True,
    )
