import json
from pathlib import Path

from livekit_agent_simulator.web.cues import build_cues_payload, write_cues_json


def _write_report(tmp: Path) -> Path:
    rd = tmp / "smoke-hello-20260711-120000-abcd"
    rd.mkdir(parents=True)
    events = [
        {"kind": "run.started", "ts_mono_ms": 0, "turn": 0, "source": "mcp", "spec": {}},
        {
            "kind": "transcript.agent.final",
            "ts_mono_ms": 5000,
            "turn": 1,
            "source": "lk.transcription",
            "spec": {"text": "Hello there", "final": True},
        },
        {
            "kind": "transcript.user.final",
            "ts_mono_ms": 9000,
            "turn": 2,
            "source": "sim.gemini",
            "spec": {"text": "Hi, is this support?", "final": True},
        },
    ]
    (rd / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (rd / "meta.json").write_text(
        json.dumps(
            {
                "scenario_id": "smoke-hello",
                "audio": {
                    "duration_ms": 15000,
                    "t0_mono_ms": 2000,
                    "channels": {"left": "sim", "right": "agent"},
                },
            }
        ),
        encoding="utf-8",
    )
    return rd


def test_build_cues_applies_audio_offset(tmp_path: Path) -> None:
    rd = _write_report(tmp_path)
    payload = build_cues_payload(rd)
    assert payload["run_id"] == rd.name
    assert payload["audio"]["t0_mono_ms"] == 2000
    assert len(payload["cues"]) == 2
    assert payload["cues"][0]["role"] == "agent"
    assert payload["cues"][0]["start_ms"] == 3000  # 5000 - 2000
    assert payload["cues"][0]["text"] == "Hello there"
    assert payload["cues"][1]["start_ms"] == 7000
    assert payload["cues"][0]["end_ms"] == 7000


def test_write_cues_json(tmp_path: Path) -> None:
    rd = _write_report(tmp_path)
    out = write_cues_json(rd)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["cues"]
