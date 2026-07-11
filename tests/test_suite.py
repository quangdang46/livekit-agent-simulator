import json
from pathlib import Path

from livekit_agent_simulator.suite import (
    build_suite_report,
    evaluate_run_result,
    suite_report_markdown,
    write_suite_report,
)


def _ok_result(**overrides: object) -> dict:
    base = {
        "executed": True,
        "status": "done",
        "run_id": "smoke-hello-20260101-000000-aaaa",
        "validation": {"valid": True, "id": "smoke-hello"},
        "summary": {
            "run_id": "smoke-hello-20260101-000000-aaaa",
            "status": "done",
            "duration_ms": 1000,
            "turn_count": 2,
            "assert_verify": {"pass": True, "skipped": False},
            "script_verify": {"pass": True},
            "verdict": {"verdict": "pass", "score": 100},
        },
    }
    base.update(overrides)
    return base


def test_gate_pass() -> None:
    g = evaluate_run_result(_ok_result())
    assert g["ok"] is True
    assert g["gate"] == "pass"


def test_gate_assert_hard_fail() -> None:
    r = _ok_result()
    r["summary"]["assert_verify"] = {"pass": False, "skipped": False}
    g = evaluate_run_result(r)
    assert g["ok"] is False
    assert "assert_verify" in g["hard_reasons"]


def test_gate_script_hard_fail() -> None:
    r = _ok_result()
    r["summary"]["script_verify"] = {"pass": False}
    g = evaluate_run_result(r)
    assert g["ok"] is False
    assert "script_verify" in g["hard_reasons"]


def test_gate_judge_soft_by_default() -> None:
    r = _ok_result()
    r["summary"]["verdict"] = {"verdict": "fail", "score": 0}
    g = evaluate_run_result(r, strict_judge=False)
    assert g["ok"] is True
    assert g["soft_fail"] is True
    assert "judge_fail" in g["soft_reasons"]


def test_gate_judge_hard_when_strict() -> None:
    r = _ok_result()
    r["summary"]["verdict"] = {"verdict": "fail", "score": 0}
    g = evaluate_run_result(r, strict_judge=True)
    assert g["ok"] is False
    assert "judge_fail" in g["hard_reasons"]


def test_gate_status_failed() -> None:
    r = _ok_result(status="failed")
    g = evaluate_run_result(r)
    assert g["ok"] is False
    assert any(x.startswith("status:") for x in g["hard_reasons"])


def test_suite_matrix(tmp_path: Path) -> None:
    good = _ok_result()
    bad = _ok_result()
    bad["summary"]["assert_verify"] = {"pass": False, "skipped": False}
    bad["validation"] = {"valid": True, "id": "bad-case"}
    soft = _ok_result()
    soft["summary"]["verdict"] = {"verdict": "fail", "score": 40}
    soft["validation"] = {"valid": True, "id": "soft-judge"}

    report = build_suite_report([good, bad, soft], strict_judge=False)
    assert report["totals"]["total"] == 3
    assert report["totals"]["failed_hard"] == 1
    assert report["totals"]["failed_soft_judge"] == 1
    assert report["ok"] is False
    assert report["exit_code"] == 1

    report2 = build_suite_report([good, soft], strict_judge=False)
    assert report2["ok"] is True
    assert report2["exit_code"] == 0

    paths = write_suite_report(report, tmp_path, stem="suite-test")
    assert Path(paths["json"]).exists()
    data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert data["suite"] is True
    md = suite_report_markdown(report)
    assert "Suite report" in md
    assert "bad-case" in md or "smoke-hello" in md
