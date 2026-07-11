from pathlib import Path

import pytest

from livekit_agent_simulator.audio.cue_catalog import (
    BUILTIN_ALIASES,
    list_all_cues,
    list_package_cues,
    resolve_cue_asset,
)
from livekit_agent_simulator.config import CuesConfig


def test_resolve_builtin_prefix() -> None:
    p = resolve_cue_asset("builtin:noise.loud")
    assert p.is_file()
    assert p.name == "loud_noise_burst.wav"


def test_resolve_vocal_voice_aliases() -> None:
    for alias, fname in (
        ("voice.barge_short", "barge_wait_en.wav"),
        ("voice.barge_sorry", "barge_sorry_en.wav"),
        ("voice.backchannel", "backchannel_uhhuh_en.wav"),
        ("voice.barge_vi", "barge_wait_vi.wav"),
    ):
        p = resolve_cue_asset(f"builtin:{alias}")
        assert p.is_file(), alias
        assert p.name == fname
        assert BUILTIN_ALIASES[alias] == fname


def test_resolve_at_alias() -> None:
    p = resolve_cue_asset("@noise.ambient")
    assert p.is_file()
    assert p.name == BUILTIN_ALIASES["noise.ambient"]


def test_target_overrides_package(tmp_path: Path) -> None:
    # Create fake package-style layout via scenario + target cues
    root = tmp_path / "proj"
    cues = root / ".agent-sim" / "cues"
    cues.mkdir(parents=True)
    # Minimal valid-enough wav header not required — just a file for path resolve
    custom = cues / "loud_noise_burst.wav"
    custom.write_bytes(b"RIFF_fake_override")
    resolved = resolve_cue_asset(
        "loud_noise_burst.wav",
        project_root=root,
    )
    assert resolved == custom.resolve()


def test_config_alias(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    cues = root / ".agent-sim" / "cues"
    cues.mkdir(parents=True)
    wav = cues / "office.wav"
    wav.write_bytes(b"RIFF_office")
    cfg = CuesConfig(aliases={"office": "office.wav"})
    resolved = resolve_cue_asset("office", project_root=root, cues_config=cfg)
    assert resolved == wav.resolve()


def test_scenario_dir_wins_over_package(tmp_path: Path) -> None:
    scen = tmp_path / "scenarios"
    scen.mkdir()
    local = scen / "ambient_noise_bed.wav"
    local.write_bytes(b"RIFF_local")
    resolved = resolve_cue_asset("ambient_noise_bed.wav", scenario_dir=scen)
    assert resolved == local.resolve()


def test_list_package_cues_nonempty() -> None:
    items = list_package_cues()
    assert any(i["id"] == "noise.loud" for i in items)


def test_list_all_cues_structure() -> None:
    data = list_all_cues(None)
    assert "builtin" in data
    assert "resolve_order" in data
    assert data["builtin"]


def test_missing_raises() -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        resolve_cue_asset("builtin:does_not_exist_xyz")
