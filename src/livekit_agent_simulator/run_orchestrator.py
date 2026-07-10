"""End-to-end run: preflight → room+dispatch → wait agent → sim join → converse → report.

End conditions (first one wins):
    - simulator persona says goodbye and emits [END_CALL]
    - scenario max_turns reached (after the agent replied in the final turn)
    - scenario timeout_s exceeded
    - agent participant disconnected / room closed
    - dead call: no agent activity for 3 × silence_threshold_ms
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import SimConfig, config_snapshot
from .gemini.judge import judge_run
from .gemini.live_session import GeminiCallerBridge
from .livekit.adapter import SIM_IDENTITY, AgentJoinTimeout, LiveKitAdapter
from .livekit.observer import Observer
from .logging.event_writer import EventWriter
from .logging.sqlite_store import RunStore
from .preflight import run_preflight
from .scenario import Scenario, SimulatorSpec, find_scenario
from .script_runner import ScriptRunner, evaluate_script_log


def new_run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


async def run_scenario(cfg: SimConfig, scenario_id: str) -> dict[str, Any]:
    """Run one scenario. Returns {run_id, status, report_dir, summary}."""
    preflight, _ = await run_preflight(cfg.project_root, connectivity=True)
    if not preflight.ok:
        failed = [c for c in preflight.checks if c["status"] == "fail"]
        raise RuntimeError("Preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))

    scenario = find_scenario(cfg.scenarios_dir, scenario_id)
    run = scenario.run_spec
    dispatch_metadata = scenario.dispatch_metadata(cfg.livekit.dispatch_metadata)
    run_id = new_run_id()
    report_dir = cfg.reports_dir / run_id
    writer = EventWriter(
        run_id,
        report_dir,
        timezone_name=cfg.observe.timezone,
        turn_taking_warn_ms=cfg.observe.turn_taking_warn_ms,
    )
    store = RunStore(cfg.sqlite_path)

    started_utc = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": scenario.id,
        "scenario_file": str(scenario.path),
        "run_spec": {
            "max_turns": run.max_turns,
            "timeout_s": run.timeout_s,
            "first_speaker": run.first_speaker,
        },
        "dispatch_metadata_set": bool(dispatch_metadata),
        "agent_name": cfg.livekit.agent_name,
        "started_utc": started_utc,
        "config_snapshot": config_snapshot(cfg),
    }

    status = "failed"
    verdict: dict[str, Any] | None = None
    summary: dict[str, Any] = {}

    async with LiveKitAdapter(cfg) as adapter:
        writer.emit(
            "run.started",
            spec={"scenario_id": scenario.id, "config_snapshot": config_snapshot(cfg)},
            include_dialogue=False,
        )
        try:
            dispatch = await adapter.create_room_and_dispatch(run_id, dispatch_metadata)
            meta["room_name"] = dispatch.room_name
            writer.emit(
                "dispatch.created",
                spec={
                    "room": dispatch.room_name,
                    "agent_name": cfg.livekit.agent_name,
                    "dispatch_id": dispatch.dispatch_id,
                    "metadata_set": bool(dispatch_metadata),
                },
                include_dialogue=False,
            )
            await store.create_run(
                run_id, scenario.id, dispatch.room_name, cfg.livekit.agent_name,
                started_utc, str(report_dir),
            )

            try:
                agent_identity = await adapter.wait_for_agent(dispatch.room_name)
            except AgentJoinTimeout as e:
                writer.emit("dispatch.agent_timeout", spec={"error": str(e)}, include_dialogue=False)
                raise
            writer.emit(
                "dispatch.agent_joined", spec={"identity": agent_identity}, include_dialogue=False
            )
            meta["agent_identity"] = agent_identity

            room = await adapter.connect_simulator(dispatch.room_name)
            writer.emit(
                "sim.connected",
                spec={"identity": SIM_IDENTITY, "room": dispatch.room_name},
                include_dialogue=False,
            )

            observer = Observer(
                room,
                writer,
                cfg.observe,
                agent_identity,
                SIM_IDENTITY,
                first_speaker=run.first_speaker,
            )
            observer.attach()

            bridge = GeminiCallerBridge(
                cfg,
                room,
                observer,
                writer,
                persona_system_prompt=scenario.persona_system_prompt(),
                first_speaker=run.first_speaker,
            )
            bridge.watch_agent_tracks(agent_identity)

            script_runner: ScriptRunner | None = None
            script_task: asyncio.Task | None = None
            if scenario.script_steps:
                script_runner = ScriptRunner(
                    scenario.script_steps,
                    observer,
                    bridge,
                    writer,
                    scenario_dir=scenario.path.parent,
                )
                script_task = asyncio.create_task(script_runner.run(), name="script-runner")

            bridge_task = asyncio.create_task(bridge.run(), name="gemini-bridge")
            try:
                end_reason = await _conversation_loop(
                    scenario, run, observer, bridge, writer, cfg.observe.silence_threshold_ms / 1000
                )
            finally:
                if script_runner is not None:
                    script_runner.stop()
                if script_task is not None:
                    script_task.cancel()
                    await asyncio.gather(script_task, return_exceptions=True)
                bridge.stop()
                await asyncio.wait_for(asyncio.shield(_settle(bridge_task)), timeout=10)

            writer.emit("run.end_condition", spec={"reason": end_reason}, include_dialogue=False)
            await room.disconnect()
            status = "done"
        except Exception as e:
            writer.emit(
                "run.error",
                spec={"error": f"{type(e).__name__}: {e}"},
                include_dialogue=False,
            )
            status = "failed"
        finally:
            if meta.get("room_name"):
                await adapter.delete_room(meta["room_name"])

    if status == "done" and scenario.script_steps:
        script_verify = evaluate_script_log(
            writer.events, scenario.script_steps, scenario.script_verify
        )
        writer.emit("script.verify", spec=script_verify, include_dialogue=False)
        summary_extra = {"script_verify": script_verify}
    else:
        summary_extra = {}

    if status == "done" and cfg.judge is not None and scenario.pass_criteria:
        try:
            tool_events = [e for e in writer.events if e["kind"].startswith("tool.")]
            verdict = await judge_run(
                cfg.judge,
                cfg.simulator.google_api_key,
                scenario.pass_criteria,
                writer.turn_metrics(),
                tool_events,
            )
            writer.emit("judge.verdict", spec=verdict or {}, include_dialogue=False)
        except Exception as e:
            verdict = {"verdict": "error", "notes": f"{type(e).__name__}: {e}"}

    summary = writer.finalize(status, meta=meta, verdict=verdict)
    if summary_extra:
        summary.update(summary_extra)
        (report_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    ended_utc = datetime.now(timezone.utc).isoformat()
    await store.insert_events(run_id, writer.events)
    await store.insert_turns(run_id, writer.turn_metrics())
    await store.finish_run(run_id, status, summary, ended_utc)

    return {
        "run_id": run_id,
        "status": status,
        "report_dir": str(report_dir),
        "summary": summary,
    }


async def _settle(task: asyncio.Task) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def _conversation_loop(
    scenario: Scenario,
    run: SimulatorSpec,
    observer: Observer,
    bridge: GeminiCallerBridge,
    writer: EventWriter,
    cfg_silence_s: float,
) -> str:
    """Poll every 250 ms until one end condition fires. Returns the reason."""
    deadline = time.monotonic() + run.timeout_s
    silence_reported_at: float | None = None

    while True:
        if bridge.end_call.is_set():
            return "sim_end_call"
        if observer.agent_disconnected.is_set():
            return "agent_disconnected"
        if observer.turn >= run.max_turns and observer.agent_replied_this_turn:
            return "max_turns"
        if time.monotonic() > deadline:
            return "timeout"

        silent_for = time.monotonic() - observer.last_agent_activity_mono
        if silent_for >= cfg_silence_s:
            if silence_reported_at is None or (time.monotonic() - silence_reported_at) >= cfg_silence_s:
                writer.emit(
                    "silence.detected",
                    spec={"duration_ms": int(silent_for * 1000)},
                    source="observer",
                )
                silence_reported_at = time.monotonic()
            if silent_for >= cfg_silence_s * 3:
                return "dead_call_silence"
        else:
            silence_reported_at = None

        await asyncio.sleep(0.25)
