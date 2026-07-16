import pytest

from livekit_agent_simulator.scenario import ScenarioError, parse_scenario

VALID = """\
{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"smoke-hello","locale":"ja-JP","tags":["smoke"]}}
{"kind":"Persona","spec":{"name":"Tanaka","brief":"First-time caller confirming the business.","goals":["hear greeting"],"style":"polite"}}
{"kind":"Simulator","spec":{"max_turns":2,"timeout_s":90,"first_speaker":"agent"}}
{"kind":"PassCriteria","spec":{"criteria":["agent greets the caller"]}}
"""


def test_parse_valid(tmp_path):
    f = tmp_path / "smoke-hello.jsonl"
    f.write_text(VALID, encoding="utf-8")
    s = parse_scenario(f)
    assert s.id == "smoke-hello"
    assert s.simulator.max_turns == 2
    assert s.pass_criteria == ["agent greets the caller"]
    prompt = s.persona_system_prompt()
    assert "Tanaka" in prompt
    assert "ja-JP" in prompt
    assert "[END_CALL]" in prompt
    assert 'NEVER pronounce the English words "end call"' in prompt


def test_execute_overrides_simulator(tmp_path):
    content = VALID.replace(
        '{"kind":"PassCriteria"',
        '{"kind":"Execute","spec":{"max_turns":4,"first_speaker":"user"}}\n{"kind":"PassCriteria"',
    )
    f = tmp_path / "exec.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    assert s.run_spec.max_turns == 4
    assert s.run_spec.first_speaker == "user"
    assert "You speak first" in s.persona_system_prompt()


def test_dispatch_metadata_json(tmp_path):
    content = (
        VALID.replace('"agent greets the caller"', '"ok"')
        + '\n{"kind":"Dispatch","spec":{"metadata":"{\\"yourProjectKey\\":\\"abc\\"}"}}\n'
    )
    f = tmp_path / "dispatch.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    assert s.dispatch_metadata("default") == '{"yourProjectKey":"abc"}'
    assert s.export_dict()["dispatch"]["metadata_set"] is True


def test_invalid_dispatch_metadata(tmp_path):
    content = VALID + '\n{"kind":"Dispatch","spec":{"metadata":"not-json"}}\n'
    f = tmp_path / "bad-dispatch.jsonl"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(ScenarioError, match="Dispatch.spec.metadata"):
        parse_scenario(f)


def test_missing_persona_brief(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(VALID.replace('"brief":"First-time caller confirming the business.",', ""), encoding="utf-8")
    with pytest.raises(ScenarioError, match="brief"):
        parse_scenario(f)


def test_invalid_json_line_number(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(VALID + "{not json}\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match=":5"):
        parse_scenario(f)


def test_wrong_api_version(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(VALID.replace("agent-sim/v1", "agent-sim/v0"), encoding="utf-8")
    with pytest.raises(ScenarioError, match="apiVersion"):
        parse_scenario(f)


def test_parse_script_section(tmp_path):
    content = (
        VALID.replace('"agent greets the caller"', '"ok"')
        + '\n{"kind":"Script","spec":{"steps":[{"id":"bc","say":"うん","delay_ms":500}],"verify":{"min_agent_finals_after_first_cue":1}}}\n'
    )
    f = tmp_path / "script.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    assert len(s.script_steps) == 1
    assert s.script_steps[0].say == "うん"
    assert s.script_verify is not None
    assert s.script_verify.min_agent_finals_after_first_cue == 1
    assert "SCRIPT OVERLAY" in s.persona_system_prompt() or "SIMULATOR CUE" in s.persona_system_prompt()
