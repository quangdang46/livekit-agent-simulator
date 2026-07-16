from pathlib import Path

from livekit_agent_simulator.run_orchestrator import (
    allocate_run_dir,
    new_run_id,
    next_run_seq,
)


def test_new_run_id_seq_prefix_plus_scenario() -> None:
    rid = new_run_id("sp-vad-rt-barge-early", seq=1)
    assert rid == "001-sp-vad-rt-barge-early"


def test_new_run_id_sanitizes_weird_ids() -> None:
    rid = new_run_id("My Case!!/../x", seq=7)
    assert rid == "007-my-case-x"


def test_new_run_id_name_override() -> None:
    rid = new_run_id("sp-vad-rt-barge-early", name="demo", seq=1)
    assert rid == "001-demo"
    assert "sp-vad-rt-barge-early" not in rid


def test_new_run_id_sanitizes_name_override() -> None:
    rid = new_run_id("smoke-hello", name="Run #1!!", seq=3)
    assert rid == "003-run-1"


def test_next_run_seq_from_reports_dir(tmp_path: Path) -> None:
    (tmp_path / "001-smoke-hello").mkdir()
    (tmp_path / "003-demo").mkdir()
    (tmp_path / "legacy-no-seq-folder").mkdir()
    (tmp_path / "suite-20260716.json").write_text("{}", encoding="utf-8")
    assert next_run_seq(tmp_path) == 4
    assert next_run_seq(tmp_path / "missing") == 1


def test_allocate_run_dir_increments_on_collision(tmp_path: Path) -> None:
    first_id, first_dir = allocate_run_dir(tmp_path, "smoke-hello")
    assert first_id == "001-smoke-hello"
    assert first_dir.is_dir()

    second_id, second_dir = allocate_run_dir(tmp_path, "smoke-hello")
    assert second_id == "002-smoke-hello"
    assert second_dir.is_dir()

    named_id, _ = allocate_run_dir(tmp_path, "smoke-hello", name="demo")
    assert named_id == "003-demo"
