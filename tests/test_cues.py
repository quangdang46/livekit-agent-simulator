import json
from pathlib import Path

from livekit_agent_simulator.web.cues import build_cues_payload, write_cues_json


def _write_report(tmp: Path, *, with_markers: bool = False) -> Path:
    rd = tmp / "smoke-hello-20260711-120000-abcd"
    rd.mkdir(parents=True)
    events: list[dict] = [
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
    if with_markers:
        events.insert(
            2,
            {
                "kind": "sim.script.cue",
                "ts_mono_ms": 5200,
                "turn": 1,
                "source": "sim.script",
                "spec": {
                    "step_id": "soft-barge",
                    "label": "backchannel-barge",
                    "say": "uh-huh",
                    "trigger": "agent_speaking",
                    "action": "speak",
                    "barge_in": True,
                    "waited_ms": 400,
                    "during_agent_speech": True,
                },
            },
        )
        events.append(
            {
                "kind": "sim.script.wait",
                "ts_mono_ms": 8000,
                "turn": 1,
                "source": "sim.script",
                "spec": {
                    "step_id": "pause",
                    "label": "user-pause",
                    "trigger": "silence",
                    "action": "wait",
                    "waited_ms": 1500,
                    "barge_in": False,
                },
            }
        )
        events.append(
            {
                "kind": "silence.detected",
                "ts_mono_ms": 10000,
                "turn": 2,
                "source": "observer",
                "spec": {"duration_ms": 4000},
            }
        )
        events.append(
            {
                "kind": "interruption",
                "ts_mono_ms": 5500,
                "turn": 1,
                "source": "sim",
                "spec": {"by": "agent", "note": "Gemini output interrupted"},
            }
        )
        events.append(
            {
                "kind": "transcript.agent.final",
                "ts_mono_ms": 11000,
                "turn": 3,
                "source": "lk.transcription",
                "spec": {"text": "Sure, how can I help?", "final": True},
            }
        )
        events.append(
            {
                "kind": "script.verify",
                "ts_mono_ms": 12000,
                "source": "mcp",
                "spec": {
                    "pass": True,
                    "agent_finals_after_barge_in": 1,
                    "agent_finals_after_silence": 1,
                    "checks": [],
                },
            }
        )
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
    if with_markers:
        (rd / "summary.json").write_text(
            json.dumps(
                {
                    "script_verify": {
                        "pass": True,
                        "agent_finals_after_barge_in": 1,
                    },
                    "assert_verify": {"pass": True, "skipped": False, "checks": []},
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
    assert payload["markers"] == []


def test_build_markers_barge_silence_recovery(tmp_path: Path) -> None:
    rd = _write_report(tmp_path, with_markers=True)
    payload = build_cues_payload(rd)
    types = [m["type"] for m in payload["markers"]]
    assert "barge_in" in types
    assert "silence_wait" in types
    assert "silence" in types
    assert "interruption" in types
    assert "recovery" in types

    barge = next(m for m in payload["markers"] if m["type"] == "barge_in")
    assert barge["start_ms"] == 3200  # 5200 - 2000
    assert "backchannel-barge" in barge["label"]
    assert barge["say"] == "uh-huh"

    wait = next(m for m in payload["markers"] if m["type"] == "silence_wait")
    assert wait["end_ms"] >= wait["start_ms"]
    assert "user-pause" in wait["label"]

    silence = next(m for m in payload["markers"] if m["type"] == "silence")
    assert silence["duration_ms"] == 4000
    assert silence["start_ms"] == 4000  # 10000-2000-4000

    recovery = next(m for m in payload["markers"] if m["type"] == "recovery")
    assert recovery["start_ms"] == 9000  # first agent final after barge (11000-2000)

    assert payload["marker_counts"]["barge_in"] == 1
    assert payload["script_verify"]["pass"] is True
    assert payload["assert_verify"]["pass"] is True

    # Agent cue near barge should get tags
    agent0 = payload["cues"][0]
    assert agent0["role"] == "agent"
    assert "barge_in" in (agent0.get("marker_tags") or [])


def test_write_cues_json(tmp_path: Path) -> None:
    rd = _write_report(tmp_path, with_markers=True)
    out = write_cues_json(rd)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["cues"]
    assert data["markers"]
