"""Caller / Telephony parse, merge, and fail-fast validation."""

import pytest

from livekit_agent_simulator.config import (
    LiveKitConfig,
    ObserveConfig,
    SimConfig,
    SimulatorConfig,
    SimulatorVoiceConfig,
    TelephonyConfig,
)
from livekit_agent_simulator.scenario import (
    ScenarioError,
    effective_telephony,
    parse_scenario,
    validate_telephony_for_mode,
)
from livekit_agent_simulator.scenario_from_dict import scenario_from_dict


BASE = """\
{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"tel-demo","locale":"en-US","tags":["t"]}}
{"kind":"Persona","spec":{"name":"A","brief":"caller brief"}}
{"kind":"Execute","spec":{"max_turns":2,"timeout_s":60,"first_speaker":"user"}}
"""


def _cfg(tmp_path, **tel):
    return SimConfig(
        project_root=tmp_path,
        livekit=LiveKitConfig(
            url="wss://demo.livekit.cloud",
            api_key="k",
            api_secret="s",
            agent_name="agent",
        ),
        simulator=SimulatorConfig(google_api_key="AIza", voice=SimulatorVoiceConfig()),
        observe=ObserveConfig(),
        telephony=TelephonyConfig(**tel) if tel else TelephonyConfig(),
    )


def test_default_mode_webrtc(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(BASE, encoding="utf-8")
    s = parse_scenario(f)
    assert s.effective_caller_mode() == "webrtc_sim"
    assert s.export_dict()["caller_mode"] == "webrtc_sim"


def test_parse_outbound_caller(tmp_path):
    content = BASE + (
        '{"kind":"Caller","spec":{"mode":"outbound_sip"}}\n'
        '{"kind":"Telephony","spec":{"call_to":"+15551112222","prepare_ms":1000}}\n'
    )
    f = tmp_path / "out.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    assert s.effective_caller_mode() == "outbound_sip"
    assert s.telephony is not None
    assert s.telephony.call_to == "+15551112222"
    assert s.telephony.prepare_ms == 1000


def test_invalid_mode(tmp_path):
    content = BASE + '{"kind":"Caller","spec":{"mode":"pstn_only"}}\n'
    f = tmp_path / "bad.jsonl"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(ScenarioError, match="Caller.spec.mode"):
        parse_scenario(f)


def test_merge_scenario_over_config(tmp_path):
    content = BASE + (
        '{"kind":"Caller","spec":{"mode":"inbound_sip"}}\n'
        '{"kind":"Telephony","spec":{"dial_in":"+1555999","sip_trunk_id":"ST_scen"}}\n'
    )
    f = tmp_path / "in.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    cfg = _cfg(
        tmp_path,
        outbound_trunk_id="ST_cfg",
        dial_in="+1555000",
        prepare_ms=5000,
    )
    tel = effective_telephony(s, cfg)
    assert tel.dial_in == "+1555999"
    assert tel.outbound_trunk_id == "ST_scen"
    assert tel.prepare_ms == 5000  # scenario omitted prepare → config


def test_merge_config_defaults_when_scenario_omits(tmp_path):
    content = BASE + '{"kind":"Caller","spec":{"mode":"outbound_sip"}}\n'
    f = tmp_path / "out2.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    cfg = _cfg(
        tmp_path,
        outbound_trunk_id="ST_cfg",
        sim_inbound_number="+1555888",
        prepare_ms=2500,
        wait_until_answered=False,
    )
    tel = effective_telephony(s, cfg)
    assert tel.call_to == "+1555888"
    assert tel.outbound_trunk_id == "ST_cfg"
    assert tel.prepare_ms == 2500
    assert tel.wait_until_answered is False


def test_validate_outbound_missing_call_to(tmp_path):
    content = BASE + '{"kind":"Caller","spec":{"mode":"outbound_sip"}}\n'
    f = tmp_path / "miss.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    cfg = _cfg(tmp_path, outbound_trunk_id="ST_x")
    with pytest.raises(ScenarioError, match="call_to"):
        validate_telephony_for_mode(s, cfg)


def test_validate_inbound_missing_trunk(tmp_path):
    content = BASE + (
        '{"kind":"Caller","spec":{"mode":"inbound_sip"}}\n'
        '{"kind":"Telephony","spec":{"dial_in":"+1555"}}\n'
    )
    f = tmp_path / "miss2.jsonl"
    f.write_text(content, encoding="utf-8")
    s = parse_scenario(f)
    cfg = _cfg(tmp_path)
    with pytest.raises(ScenarioError, match="outbound_trunk"):
        validate_telephony_for_mode(s, cfg)


def test_scenario_from_dict_caller():
    s = scenario_from_dict(
        {
            "id": "dyn",
            "persona": {"brief": "hi"},
            "caller": {"mode": "outbound_sip"},
            "telephony": {"call_to": "+1", "sip_trunk_id": "ST_1"},
        }
    )
    assert s.effective_caller_mode() == "outbound_sip"
    assert s.telephony.call_to == "+1"
