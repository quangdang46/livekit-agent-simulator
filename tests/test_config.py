import pytest

from livekit_agent_simulator.config import (
    ConfigError,
    ObserveConfig,
    config_snapshot,
    load_config,
)

VALID_CONFIG = """
project: demo
livekit:
  url: "wss://demo.livekit.cloud"
  api_key: "APIkey"
  api_secret: "secret"
  agent_name: "my-agent-local"
simulator:
  google_api_key: "AIzaTest"
  language: "en-US"
  voice:
    model: "gemini-3.1-flash-live-preview"
    voice: "Puck"
judge:
  model: "gemini-2.5-flash"
observe:
  timezone: "UTC"
  record_audio: true
  data_topics: ["app.events"]
  tool_event_patterns:
    - match: { topic: "app.events", type: "tool_started" }
      emit: tool.start
  silence_threshold_ms: 3000
"""


def _write(tmp_path, text):
    dot = tmp_path / ".agent-sim"
    dot.mkdir()
    (dot / "config.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_load_valid_config(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert cfg.livekit.agent_name == "my-agent-local"
    assert cfg.livekit.agent_join_timeout_ms == 25_000  # default
    assert cfg.simulator.voice.model == "gemini-3.1-flash-live-preview"
    assert cfg.judge is not None and cfg.judge.model == "gemini-2.5-flash"
    assert cfg.observe.silence_threshold_ms == 3000
    assert cfg.observe.record_audio is True
    assert cfg.observe.audio_recording_enabled is True
    assert cfg.observe.lk_agent_session is True
    assert cfg.observe.tool_event_patterns[0].emit == "tool.start"
    assert cfg.sqlite_path == tmp_path / ".agent-sim" / "runs.sqlite"
    assert config_snapshot(cfg)["observe"]["record_audio"] is True
    assert config_snapshot(cfg)["observe"]["lk_agent_session"] is True


def test_record_audio_defaults_true_when_omitted(tmp_path):
    cfg_text = VALID_CONFIG.replace("  record_audio: true\n", "")
    cfg = load_config(_write(tmp_path, cfg_text))
    assert cfg.observe.record_audio is True
    assert cfg.observe.audio_recording_enabled is True


def test_record_audio_can_be_disabled(tmp_path):
    cfg_text = VALID_CONFIG.replace("  record_audio: true", "  record_audio: false")
    cfg = load_config(_write(tmp_path, cfg_text))
    assert cfg.observe.record_audio is False
    assert cfg.observe.audio_recording_enabled is False


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="lk-sim init"):
        load_config(tmp_path)


def test_missing_agent_name(tmp_path):
    broken = VALID_CONFIG.replace('agent_name: "my-agent-local"', "")
    with pytest.raises(ConfigError, match="livekit.agent_name"):
        load_config(_write(tmp_path, broken))


def test_missing_simulator_key(tmp_path):
    broken = VALID_CONFIG.replace('google_api_key: "AIzaTest"', "")
    with pytest.raises(ConfigError, match="simulator.google_api_key"):
        load_config(_write(tmp_path, broken))


def test_snapshot_never_leaks_secrets(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    snap = config_snapshot(cfg)
    text = str(snap)
    assert "secret" not in text
    assert "AIzaTest" not in text
    assert "APIkey" not in text
    assert snap["livekit"]["agent_name"] == "my-agent-local"


def test_tool_gap_only_when_session_and_patterns_are_disabled(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_CONFIG.replace("  tool_event_patterns:", "  lk_agent_session: false\n  tool_event_patterns:")))
    cfg.observe.tool_event_patterns = []
    assert config_snapshot(cfg)["observe_gaps"] == ["tool_events"]

    cfg.observe = ObserveConfig()
    assert config_snapshot(cfg)["observe_gaps"] == []
