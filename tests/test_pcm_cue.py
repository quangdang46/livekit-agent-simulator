import math
import struct
import wave
from pathlib import Path

import pytest

from livekit_agent_simulator.audio.pcm_cue import load_wav_pcm, resolve_cue_asset


def test_load_wav_pcm(tmp_path: Path) -> None:
    rate = 24_000
    path = tmp_path / "cue.wav"
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * 100)

    pcm, out_rate, channels = load_wav_pcm(path)
    assert out_rate == rate
    assert channels == 1
    assert len(pcm) == 200


def test_resolve_cue_asset_package_template() -> None:
    root = Path(__file__).resolve().parents[1]
    path = resolve_cue_asset("backchannel_ja.wav", scenario_dir=None, package_root=root)
    assert path.exists()


def test_parse_room_pcm_requires_asset(tmp_path: Path) -> None:
    from livekit_agent_simulator.script_parse import parse_script_steps

    with pytest.raises(ValueError, match="room_pcm"):
        parse_script_steps(
            {
                "steps": [
                    {
                        "id": "x",
                        "say": "hi",
                        "delivery": "room_pcm",
                    }
                ]
            },
            "t.jsonl:1",
        )
