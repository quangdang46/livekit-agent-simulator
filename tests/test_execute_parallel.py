"""execute_scenarios --parallel concurrency."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from livekit_agent_simulator.ops import execute_scenarios


@pytest.mark.asyncio
async def test_parallel_rejects_zero() -> None:
    with pytest.raises(ValueError, match="parallel"):
        await execute_scenarios("/tmp", scenario_ids=["a"], parallel=0)


@pytest.mark.asyncio
async def test_parallel_runs_concurrently(tmp_path) -> None:
    """With parallel=2, two scenarios should overlap in time."""
    # Minimal config so load_config works
    (tmp_path / ".agent-sim").mkdir()
    (tmp_path / ".agent-sim" / "config.yaml").write_text(
        """
livekit:
  url: wss://example.livekit.cloud
  api_key: APIkeyxxxxxxxx
  api_secret: secretxxxxxxxxxxxxxxxx
  agent_name: test-agent
simulator:
  google_api_key: AIzaxxxxxxxxxxxxxxxx
""",
        encoding="utf-8",
    )
    scen = tmp_path / ".agent-sim" / "scenarios"
    scen.mkdir()
    for sid in ("a", "b", "c"):
        (scen / f"{sid}.jsonl").write_text(
            "\n".join(
                [
                    f'{{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{{"id":"{sid}","locale":"en-US","tags":["t"]}}}}',
                    '{"kind":"Persona","spec":{"name":"X","brief":"test"}}',
                    '{"kind":"Execute","spec":{"max_turns":1,"timeout_s":30,"first_speaker":"user"}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_execute(project_root, scenario_id, *, repeat=1, pass_at_k=None):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.15)
        async with lock:
            active -= 1
        return {
            "executed": True,
            "scenario_id": scenario_id,
            "status": "done",
            "run_id": f"run-{scenario_id}",
            "validation": {"valid": True, "id": scenario_id},
            "ok": True,
            "summary": {"status": "done", "turn_count": 1},
        }

    with patch("livekit_agent_simulator.ops.execute_scenario", new=AsyncMock(side_effect=fake_execute)):
        out = await execute_scenarios(
            tmp_path,
            scenario_ids=["a", "b", "c"],
            write_report=False,
            parallel=2,
        )

    assert out["count"] == 3
    assert out["parallel"] == 2
    assert [r.get("scenario_id") or r.get("validation", {}).get("id") for r in out["results"]]
    # Order preserved
    ids = []
    for r in out["results"]:
        ids.append(r.get("scenario_id") or (r.get("validation") or {}).get("id") or r.get("run_id"))
    # fake returns scenario_id
    assert [r["scenario_id"] for r in out["results"]] == ["a", "b", "c"]
    assert max_active == 2


@pytest.mark.asyncio
async def test_parallel_default_sequential(tmp_path) -> None:
    (tmp_path / ".agent-sim").mkdir()
    (tmp_path / ".agent-sim" / "config.yaml").write_text(
        """
livekit:
  url: wss://example.livekit.cloud
  api_key: APIkeyxxxxxxxx
  api_secret: secretxxxxxxxxxxxxxxxx
  agent_name: test-agent
simulator:
  google_api_key: AIzaxxxxxxxxxxxxxxxx
""",
        encoding="utf-8",
    )
    scen = tmp_path / ".agent-sim" / "scenarios"
    scen.mkdir()
    for sid in ("a", "b"):
        (scen / f"{sid}.jsonl").write_text(
            "\n".join(
                [
                    f'{{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{{"id":"{sid}","locale":"en-US","tags":["t"]}}}}',
                    '{"kind":"Persona","spec":{"name":"X","brief":"test"}}',
                    '{"kind":"Execute","spec":{"max_turns":1,"timeout_s":30,"first_speaker":"user"}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_execute(project_root, scenario_id, *, repeat=1, pass_at_k=None):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.08)
        async with lock:
            active -= 1
        return {
            "executed": True,
            "scenario_id": scenario_id,
            "status": "done",
            "run_id": f"run-{scenario_id}",
            "validation": {"valid": True, "id": scenario_id},
            "ok": True,
            "summary": {"status": "done"},
        }

    with patch("livekit_agent_simulator.ops.execute_scenario", new=AsyncMock(side_effect=fake_execute)):
        out = await execute_scenarios(
            tmp_path,
            scenario_ids=["a", "b"],
            write_report=False,
            # default parallel=1
        )

    assert out["parallel"] == 1
    assert max_active == 1
