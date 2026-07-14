"""LLM judge — text-only Gemini scoring of the finished run against PassCriteria."""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from ..config import JudgeConfig

JUDGE_SYSTEM = """You are a strict QA judge for voice-agent test calls.
You receive: (1) the pass criteria, (2) the conversation transcript per turn,
(3) tool call spans with errors. Evaluate ONLY against the criteria.
Return JSON: {"verdict": "pass"|"fail", "score": 0-100,
"criteria": [{"criterion": str, "met": bool, "evidence": str}],
"notes": str}"""


async def judge_run(
    judge_cfg: JudgeConfig,
    google_api_key: str,
    pass_criteria: list[str],
    turns: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return await _judge(judge_cfg, google_api_key, pass_criteria, turns, tool_events, goals_met=None)


async def judge_goals(
    judge_cfg: JudgeConfig,
    google_api_key: str,
    goals: list[str],
    min_goals: int,
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Judge whether the simulated caller stated/pursued at least min_goals goals before end."""
    criteria = [f"The simulated caller stated or pursued the following goal(s) before the call ended: {goals}. Verify at least {min_goals} of {len(goals)} goals were explicitly mentioned or pursued."]
    return await _judge(judge_cfg, google_api_key, criteria, turns, tool_events=[], goals_met=True)


async def _judge(
    judge_cfg: JudgeConfig,
    google_api_key: str,
    pass_criteria: list[str],
    turns: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    goals_met: bool | None = None,
) -> dict[str, Any]:
    if not pass_criteria:
        return {"verdict": "skipped", "notes": "No criteria."}

    transcript_lines = []
    for t in turns:
        transcript_lines.append(f"Turn {t['turn']}:")
        if t.get("user_text"):
            transcript_lines.append(f"  CALLER: {t['user_text']}")
        if t.get("agent_text"):
            transcript_lines.append(f"  AGENT: {t['agent_text']}")
        if t.get("tool_errors"):
            transcript_lines.append(f"  (tool errors this turn: {t['tool_errors']})")

    tool_lines = [
        json.dumps(
            {
                "kind": e["kind"],
                "turn": e.get("turn"),
                "name": e.get("spec", {}).get("name"),
                "error": e.get("spec", {}).get("error"),
                "duration_ms": e.get("spec", {}).get("duration_ms"),
            },
            ensure_ascii=False,
        )
        for e in tool_events
    ]

    prompt = (
        "PASS CRITERIA:\n"
        + "\n".join(f"- {c}" for c in pass_criteria)
        + "\n\nTRANSCRIPT:\n"
        + ("\n".join(transcript_lines) or "(empty)")
        + "\n\nTOOL SPANS:\n"
        + ("\n".join(tool_lines) or "(none)")
        + ("\n\nNOTE: This is a goals_met check. Evaluate whether the CALLER (simulated human) stated or pursued each listed goal. Agent responses alone do not satisfy caller goals." if goals_met else "")
    )

    client = genai.Client(api_key=google_api_key)
    response = await client.aio.models.generate_content(
        model=judge_cfg.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_SYSTEM,
            temperature=judge_cfg.temperature,
            response_mime_type="application/json",
        ),
    )
    try:
        return json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return {"verdict": "error", "notes": f"Judge returned non-JSON: {(response.text or '')[:500]}"}


async def judge_run_multi(
    judge_cfg: JudgeConfig,
    google_api_key: str,
    judges: list[dict[str, Any]],
    mode: str,
    turns: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run one LLM judge per group; aggregate by mode all|majority|any.

    Each judge dict: {"id": str, "criteria": list[str]}.
    Returns combined verdict plus per-judge results (LiveKit JudgeGroup-shaped).
    """
    if not judges:
        return {"verdict": "skipped", "notes": "No judges."}

    results: list[dict[str, Any]] = []
    for j in judges:
        jid = str(j.get("id") or "judge")
        criteria = list(j.get("criteria") or [])
        one = await _judge(judge_cfg, google_api_key, criteria, turns, tool_events, goals_met=None)
        one = dict(one or {})
        one["judge_id"] = jid
        results.append(one)

    passes = [r for r in results if str(r.get("verdict") or "").lower() == "pass"]
    fails = [r for r in results if str(r.get("verdict") or "").lower() == "fail"]
    n = len(results)
    mode_l = (mode or "all").lower()
    if mode_l == "any":
        ok = len(passes) >= 1
    elif mode_l == "majority":
        ok = len(passes) > n / 2
    else:  # all
        ok = len(fails) == 0 and len(passes) == n

    scores = []
    for r in results:
        try:
            scores.append(float(r.get("score")))
        except (TypeError, ValueError):
            pass
    avg = sum(scores) / len(scores) if scores else None

    return {
        "verdict": "pass" if ok else "fail",
        "score": avg,
        "mode": mode_l,
        "judges": results,
        "passed_count": len(passes),
        "failed_count": len(fails),
        "notes": f"multi-judge mode={mode_l}: {len(passes)}/{n} passed",
    }
