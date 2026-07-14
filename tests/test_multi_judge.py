"""P1.C — PassCriteria multi-judge parse + aggregate (no live Gemini)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from livekit_agent_simulator.gemini import judge as judge_mod
from livekit_agent_simulator.scenario import parse_scenario


def test_parse_judges_and_mode(tmp_path: Path):
    p = tmp_path / "mj.jsonl"
    p.write_text(
        "\n".join(
            [
                '{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"mj","locale":"en-US"}}',
                '{"kind":"Persona","spec":{"name":"A","brief":"caller","goals":["g"]}}',
                '{"kind":"Execute","spec":{"max_turns":3}}',
                '{"kind":"PassCriteria","spec":{"mode":"majority","judges":['
                '{"id":"task","criteria":["Task completed"]},'
                '{"id":"tone","criteria":["Polite tone"]}'
                "]}}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    s = parse_scenario(p)
    assert s.pass_criteria_mode == "majority"
    assert len(s.pass_judges) == 2
    assert s.pass_judges[0]["id"] == "task"
    assert any("task" in c for c in s.pass_criteria)


@pytest.mark.asyncio
async def test_aggregate_all_majority_any(monkeypatch):
    async def fake_judge(cfg, key, criteria, turns, tools, goals_met=None):
        # pass if criterion text contains "pass"
        text = " ".join(criteria)
        if "PASS" in text:
            return {"verdict": "pass", "score": 90}
        return {"verdict": "fail", "score": 10}

    monkeypatch.setattr(judge_mod, "_judge", fake_judge)
    cfg = object()
    judges = [
        {"id": "a", "criteria": ["PASS me"]},
        {"id": "b", "criteria": ["fail me"]},
    ]
    all_v = await judge_mod.judge_run_multi(cfg, "k", judges, "all", [], [])
    assert all_v["verdict"] == "fail"
    maj = await judge_mod.judge_run_multi(cfg, "k", judges, "majority", [], [])
    # 1/2 is not > 0.5
    assert maj["verdict"] == "fail"
    any_v = await judge_mod.judge_run_multi(cfg, "k", judges, "any", [], [])
    assert any_v["verdict"] == "pass"

    both = [
        {"id": "a", "criteria": ["PASS"]},
        {"id": "b", "criteria": ["PASS too"]},
    ]
    all_ok = await judge_mod.judge_run_multi(cfg, "k", both, "all", [], [])
    assert all_ok["verdict"] == "pass"
    assert all_ok["passed_count"] == 2
