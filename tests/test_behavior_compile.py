from livekit_agent_simulator.behavior_compile import (
    apply_caller_behavior,
    compile_from_behavior_spec,
    compile_from_speech_conditions,
    merge_script_steps,
)
from livekit_agent_simulator.script_runner import ScriptStep
from livekit_agent_simulator.scenario import parse_scenario


def test_compile_speech_conditions_barge_and_noise():
    persona = {
        "brief": "x",
        "speech_conditions": {
            "noise": "builtin:noise.ambient",
            "noise_delay_ms": 3000,
            "barge_policy": "mid_agent_turn",
            "barge_after_agent_ms": 800,
            "barge_say": "Hold on",
        },
    }
    steps = compile_from_speech_conditions(persona)
    ids = [s.id for s in steps]
    assert "auto-ambient" in ids
    assert "auto-barge-1" in ids
    barge = next(s for s in steps if s.barge_in)
    assert barge.say == "Hold on"
    assert barge.with_blip is True


def test_compile_vocal_barge_asset_no_blip():
    persona = {
        "brief": "x",
        "speech_conditions": {
            "barge_policy": "mid_agent_turn",
            "barge_asset": "builtin:voice.barge_short",
            "barge_say": "[barge]",
        },
    }
    steps = compile_from_speech_conditions(persona)
    barge = next(s for s in steps if s.barge_in)
    assert barge.delivery == "room_pcm"
    assert barge.asset == "builtin:voice.barge_short"
    assert barge.with_blip is False


def test_compile_behavior_spec():
    steps = compile_from_behavior_spec(
        {
            "barge_ins": [
                {"id": "cut1", "after_agent_ms": 500, "say": "Wait", "with_blip": False}
            ],
            "user_silence": [{"id": "s1", "hold_ms": 2000, "delay_ms": 300}],
            "ambient": {"asset": "builtin:noise.loud", "delay_ms": 1000},
        }
    )
    assert any(s.id == "cut1" and s.barge_in and not s.with_blip for s in steps)
    assert any(s.id == "s1" and s.action == "wait" and s.silence_after_cue_ms == 2000 for s in steps)
    assert any(s.delivery == "room_pcm" for s in steps)


def test_explicit_script_wins_id():
    explicit = [
        ScriptStep(id="auto-barge-1", trigger="agent_speaking", delay_ms=100, say="CUSTOM", barge_in=True)
    ]
    compiled = compile_from_speech_conditions(
        {"speech_conditions": {"barge_policy": "mid_agent_turn", "barge_say": "AUTO"}}
    )
    merged = merge_script_steps(explicit, compiled)
    barge = next(s for s in merged if s.id == "auto-barge-1")
    assert barge.say == "CUSTOM"


def test_parse_scenario_compiles_character(tmp_path):
    p = tmp_path / "char.jsonl"
    p.write_text(
        "\n".join(
            [
                '{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"char","locale":"en-US"}}',
                '{"kind":"Persona","spec":{"name":"A","brief":"caller","goals":["g"],"traits":["impatient"],'
                '"constraints":["No card numbers"],'
                '"speech_conditions":{"barge_policy":"mid_agent_turn","barge_say":"Quick question"}}}',
                '{"kind":"Execute","spec":{"max_turns":3,"first_speaker":"agent"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    s = parse_scenario(p)
    assert any(st.barge_in for st in s.script_steps)
    prompt = s.persona_system_prompt()
    assert "No card numbers" in prompt
    assert "Hard constraints" in prompt
    assert s.script_verify is not None
    assert s.script_verify.min_agent_finals_after_barge_in >= 1


def test_apply_caller_behavior_with_behavior_kind():
    steps, verify = apply_caller_behavior(
        {"brief": "x"},
        {"barge_ins": [{"id": "b1", "say": "Hey", "after_agent_ms": 400}]},
        [],
        None,
    )
    assert any(s.id == "b1" for s in steps)
    assert verify is not None
