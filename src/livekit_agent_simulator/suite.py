"""Suite matrix + CI gate for execute / execute-all.

Hard fails (default CI exit ≠ 0):
  - not executed / validation invalid
  - run ``status`` not ``done``
  - ``assert_verify.pass`` is false
  - ``script_verify.pass`` is false

Soft fails (recorded, do not fail CI unless ``strict_judge``):
  - LLM ``verdict.verdict == "fail"``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    s = result.get("summary")
    return s if isinstance(s, dict) else {}


def evaluate_run_result(
    result: dict[str, Any],
    *,
    strict_judge: bool = False,
) -> dict[str, Any]:
    """Score one execute_scenario result for CI."""
    hard: list[str] = []
    soft: list[str] = []

    if not result.get("executed"):
        hard.append("not_executed")
        err = result.get("error") or (result.get("validation") or {}).get("error")
        if err:
            hard.append(f"error:{err}")
    else:
        val = result.get("validation")
        if isinstance(val, dict) and val.get("valid") is False:
            hard.append("validation_failed")

    status = result.get("status")
    if result.get("executed") and status is not None and status != "done":
        hard.append(f"status:{status}")

    summary = _summary(result)
    av = summary.get("assert_verify")
    if isinstance(av, dict) and av.get("skipped") is not True and av.get("pass") is False:
        hard.append("assert_verify")

    sv = summary.get("script_verify")
    if isinstance(sv, dict) and sv.get("pass") is False:
        hard.append("script_verify")

    verdict = summary.get("verdict")
    if isinstance(verdict, dict) and str(verdict.get("verdict") or "").lower() == "fail":
        soft.append("judge_fail")
        if strict_judge:
            hard.append("judge_fail")

    hard_fail = len(hard) > 0
    soft_fail = len(soft) > 0
    return {
        "ok": not hard_fail,
        "hard_fail": hard_fail,
        "soft_fail": soft_fail,
        "hard_reasons": hard,
        "soft_reasons": soft,
        "gate": "hard" if hard_fail else ("soft" if soft_fail else "pass"),
    }


def _scenario_id_of(result: dict[str, Any]) -> str:
    if result.get("scenario_id"):
        return str(result["scenario_id"])
    val = result.get("validation") or {}
    if isinstance(val, dict) and val.get("id"):
        return str(val["id"])
    summary = _summary(result)
    rid = summary.get("run_id") or result.get("run_id") or "?"
    # run_id is often scenario-timestamp
    if isinstance(rid, str) and "-" in rid:
        return rid.rsplit("-", 3)[0] if rid.count("-") >= 3 else str(rid)
    return str(rid)


def build_suite_report(
    results: list[dict[str, Any]],
    *,
    strict_judge: bool = False,
    tag: str | None = None,
) -> dict[str, Any]:
    """Build matrix + totals for execute_scenarios results."""
    rows: list[dict[str, Any]] = []
    passed = failed_hard = failed_soft = 0

    for r in results:
        gate = evaluate_run_result(r, strict_judge=strict_judge)
        summary = _summary(r)
        verdict = summary.get("verdict") if isinstance(summary.get("verdict"), dict) else {}
        av = summary.get("assert_verify") if isinstance(summary.get("assert_verify"), dict) else {}
        sv = summary.get("script_verify") if isinstance(summary.get("script_verify"), dict) else {}
        from .metrics import metrics_digest

        mdig = metrics_digest(
            summary.get("metrics") if isinstance(summary.get("metrics"), dict) else None
        )
        row = {
            "scenario_id": _scenario_id_of(r),
            "run_id": r.get("run_id") or summary.get("run_id"),
            "status": r.get("status") or ("error" if not r.get("executed") else "?"),
            "executed": bool(r.get("executed")),
            "gate": gate["gate"],
            "ok": gate["ok"],
            "hard_reasons": gate["hard_reasons"],
            "soft_reasons": gate["soft_reasons"],
            "assert_pass": av.get("pass"),
            "script_pass": sv.get("pass"),
            "judge_verdict": verdict.get("verdict"),
            "judge_score": verdict.get("score"),
            "duration_ms": summary.get("duration_ms") or r.get("duration_ms"),
            "turn_count": summary.get("turn_count"),
            "caller_mode": summary.get("caller_mode") or r.get("caller_mode"),
            "dial_ms": summary.get("dial_ms") or r.get("dial_ms"),
            "sip_status": summary.get("sip_status") or r.get("sip_status"),
            "ttfw_ms": mdig.get("ttfw_ms"),
            "turn_p50_ms": mdig.get("turn_p50_ms"),
            "turn_p95_ms": mdig.get("turn_p95_ms"),
            "recovery_p50_ms": mdig.get("recovery_p50_ms"),
            "barge_count": mdig.get("barge_count"),
            "barge_recovery_rate": mdig.get("barge_recovery_rate"),
            "report_dir": r.get("report_dir"),
            "error": r.get("error"),
        }
        rows.append(row)
        if gate["hard_fail"]:
            failed_hard += 1
        elif gate["soft_fail"]:
            failed_soft += 1
            passed += 1  # soft still counts as CI pass when not strict
        else:
            passed += 1

    total = len(rows)
    ok = failed_hard == 0
    return {
        "suite": True,
        "ok": ok,
        "strict_judge": strict_judge,
        "tag": tag,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "total": total,
            "passed_gate": passed,
            "failed_hard": failed_hard,
            "failed_soft_judge": failed_soft,
        },
        "matrix": rows,
        "exit_code": 0 if ok else 1,
    }


def suite_report_markdown(report: dict[str, Any]) -> str:
    """Human-readable suite matrix."""
    totals = report.get("totals") or {}
    lines = [
        f"# Suite report",
        "",
        f"- generated: `{report.get('generated_utc')}`",
        f"- strict_judge: `{report.get('strict_judge')}`",
        f"- tag: `{report.get('tag')}`",
        f"- total: **{totals.get('total', 0)}** · gate pass: **{totals.get('passed_gate', 0)}** · "
        f"hard fail: **{totals.get('failed_hard', 0)}** · soft judge fail: **{totals.get('failed_soft_judge', 0)}**",
        f"- suite ok (CI): **{report.get('ok')}** (exit {report.get('exit_code')})",
        "",
        "| scenario | gate | status | assert | script | judge | p50 | p95 | ttfw | duration | run_id |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.get("matrix") or []:
        dur = row.get("duration_ms")
        dur_s = f"{dur / 1000:.1f}s" if isinstance(dur, (int, float)) else "—"

        def _ms(v: object) -> str:
            if isinstance(v, (int, float)):
                return f"{int(v)}ms"
            return "—"

        reasons = ",".join(row.get("hard_reasons") or []) or "—"
        lines.append(
            "| {sid} | {gate} | {status} | {ap} | {sp} | {jv} | {p50} | {p95} | {ttfw} | {dur} | `{rid}` |".format(
                sid=row.get("scenario_id") or "?",
                gate=row.get("gate") or "?",
                status=row.get("status") or "?",
                ap=row.get("assert_pass"),
                sp=row.get("script_pass"),
                jv=row.get("judge_verdict") or "—",
                p50=_ms(row.get("turn_p50_ms")),
                p95=_ms(row.get("turn_p95_ms")),
                ttfw=_ms(row.get("ttfw_ms")),
                dur=dur_s,
                rid=row.get("run_id") or "—",
            )
        )
        if row.get("hard_reasons"):
            lines.append(f"| ↳ hard | {reasons} | | | | | | | | | |")
    lines.append("")
    return "\n".join(lines)


def write_suite_report(
    report: dict[str, Any],
    reports_dir: Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    """Write suite-*.json and suite-*.md under reports_dir. Returns paths."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    if not stem:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        stem = f"suite-{stamp}"
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(suite_report_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
