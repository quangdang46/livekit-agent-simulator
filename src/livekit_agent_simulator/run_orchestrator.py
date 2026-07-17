"""End-to-end run: preflight → SimLeg.connect → converse → report.

Phases: prepare → SimLeg (WebRTC | inbound_sip | outbound_human_pickup | outbound_sim_callee | agent_dials) →
SimBrain → converse → verify → judge → finalize.

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
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audio.local_recorder import DEFAULT_FILENAME, LocalConversationRecorder
from .caller_nudge import nudge_caller_after_agent_greeting
from .behavior_compile import silent_mode_enabled
from .config import SimConfig, config_snapshot
from .gemini.judge import judge_run
from .gemini.live_session import GeminiCallerBridge, resolve_voice_gain
from .livekit.adapter import AgentJoinTimeout, LiveKitAdapter
from .livekit.observer import Observer
from .livekit.sim_leg import SimLegContext, SimLegError, SimLegHandle, sim_leg_factory
from .logging.event_writer import EventWriter
from .logging.sqlite_store import RunStore
from .interrupt_rate import InterruptRateRunner, parse_interrupt_rate
from .preflight import run_preflight
from .plugins.loader import ensure_plugins_loaded
from .scenario import Scenario, SimulatorSpec, find_scenario, validate_telephony_for_mode
from .script import ScriptRunner, build_caller_behavior_summary, evaluate_script_log


_LEADING_SEQ = re.compile(r"^(\d+)-")


def _run_id_slug(value: str, *, max_len: int = 48, fallback: str = "") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip()).strip("-_.")
    slug = (slug[:max_len] if slug else fallback).lower()
    return slug


def next_run_seq(reports_dir: Path | None) -> int:
    """Next report sequence number (001, 002, …) from existing report folders."""
    if reports_dir is None or not Path(reports_dir).is_dir():
        return 1
    best = 0
    for p in Path(reports_dir).iterdir():
        if not p.is_dir():
            continue
        m = _LEADING_SEQ.match(p.name)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def _run_id_stamp() -> str:
    """UTC timestamp + short random suffix so run_id stays unique vs SQLite history."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


def new_run_id(
    scenario_id: str,
    *,
    name: str | None = None,
    reports_dir: Path | None = None,
    seq: int | None = None,
) -> str:
    """Human-readable run id.

    Default: ``{NNN}-{scenario}-{YYYYMMDD}-{HHMMSS}-{xxxx}``
    With ``name``: ``{NNN}-{name}-{YYYYMMDD}-{HHMMSS}-{xxxx}``
    (scenario id stays in meta.json only).

    ``NNN`` is an auto-incrementing prefix from ``reports_dir``.
    Pass ``seq`` to pin a number (tests / retry loops).
    The timestamp+hex suffix avoids ``runs.run_id`` UNIQUE collisions when a
    report folder was deleted but the SQLite row remains.
    """
    scenario_slug = _run_id_slug(scenario_id, fallback="scenario")
    n = seq if seq is not None else next_run_seq(reports_dir)
    prefix = f"{n:03d}"
    stamp = _run_id_stamp()
    if name:
        name_slug = _run_id_slug(name, max_len=64)
        if name_slug:
            return f"{prefix}-{name_slug}-{stamp}"
    return f"{prefix}-{scenario_slug}-{stamp}"


def allocate_run_dir(
    reports_dir: Path,
    scenario_id: str,
    *,
    name: str | None = None,
) -> tuple[str, Path]:
    """Pick a free run_id and create its report folder (safe under parallel runs)."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    seq = next_run_seq(reports_dir)
    for _ in range(10_000):
        run_id = new_run_id(scenario_id, name=name, seq=seq)
        report_dir = reports_dir / run_id
        try:
            report_dir.mkdir(parents=False)
            return run_id, report_dir
        except FileExistsError:
            seq += 1
    raise RuntimeError(f"Could not allocate a free report dir under {reports_dir}")


async def run_scenario(
    cfg: SimConfig,
    scenario_id: str,
    *,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Run one scenario by id from `.agent-sim/scenarios/`."""
    preflight, _ = await run_preflight(cfg.project_root, connectivity=True)
    if not preflight.ok:
        failed = [c for c in preflight.checks if c["status"] == "fail"]
        raise RuntimeError("Preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))
    scenario = find_scenario(cfg.scenarios_dir, scenario_id)
    return await run_scenario_instance(cfg, scenario, run_name=run_name)


async def run_scenario_instance(
    cfg: SimConfig,
    scenario: Scenario,
    *,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Run a parsed Scenario (file or in-memory). Returns {run_id, status, report_dir, summary}.

    Phases (in order):
      1. prepare  — plugins, report dir, event writer
      2–3. SimLeg  — factory(mode).connect → rooms + identities
      4. brain    — GeminiCallerBridge + optional script runner
      5. converse — turns until end condition
      6. verify   — script/assert hard checks + behavior_summary
      7. judge    — optional soft LLM verdict
      8. finalize — summary.json / sqlite / multi-room cleanup
    """
    # ── Phase 1: prepare ────────────────────────────────────────────────
    plugin_load = ensure_plugins_loaded(cfg.project_root, scenario.plugin_modules)
    run = scenario.run_spec
    dispatch_metadata = scenario.dispatch_metadata(cfg.livekit.dispatch_metadata)
    run_id, report_dir = allocate_run_dir(cfg.reports_dir, scenario.id, name=run_name)
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
        "run_name": run_name,
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
        "plugins_loaded": plugin_load,
    }

    status = "failed"
    verdict: dict[str, Any] | None = None
    summary: dict[str, Any] = {}
    recorder: LocalConversationRecorder | None = None
    observer: Observer | None = None
    session_snapshot_attempted = False
    leg_handle: SimLegHandle | None = None
    caller_mode = scenario.effective_caller_mode()
    meta["caller_mode"] = caller_mode

    async with LiveKitAdapter(cfg) as adapter:
        writer.emit(
            "run.started",
            spec={
                "scenario_id": scenario.id,
                "caller_mode": caller_mode,
                "config_snapshot": config_snapshot(cfg),
            },
            include_dialogue=False,
        )
        try:
            validate_telephony_for_mode(scenario, cfg)

            # ── Phase 2–3: SimLeg.connect (Strategy) ─────────────────────
            leg = sim_leg_factory(caller_mode)
            try:
                leg_handle = await leg.connect(
                    SimLegContext(
                        adapter=adapter,
                        cfg=cfg,
                        scenario=scenario,
                        writer=writer,
                        run_id=run_id,
                        dispatch_metadata=dispatch_metadata,
                        first_speaker=run.first_speaker,
                    )
                )
            except (AgentJoinTimeout, SimLegError) as e:
                writer.emit(
                    "dispatch.agent_timeout" if isinstance(e, AgentJoinTimeout) else "sim.leg_error",
                    spec={"error": str(e), "mode": caller_mode},
                    include_dialogue=False,
                )
                raise

            meta["room_name"] = leg_handle.agent_room_name
            meta["sim_room_name"] = leg_handle.sim_room_name
            meta["agent_identity"] = leg_handle.agent_identity
            meta["sim_identity"] = leg_handle.sim_identity
            if leg_handle.meta:
                meta["leg"] = dict(leg_handle.meta)
                if "dial_ms" in leg_handle.meta:
                    meta["dial_ms"] = leg_handle.meta["dial_ms"]

            await store.create_run(
                run_id,
                scenario.id,
                leg_handle.agent_room_name,
                cfg.livekit.agent_name,
                started_utc,
                str(report_dir),
            )

            if cfg.observe.audio_recording_enabled:
                recorder = LocalConversationRecorder()
                # Pin audio t=0 as early as possible so pre-Gemini agent media
                # (outbound greeting while sim-leg wait used to block) is on the timeline.
                recorder.mark_start()

            # Observer on agent-room: transcripts + agent WAV R-channel (works for SIP 2-room).
            # For outbound_sim_callee, agent_room was joined *before* dial inside OutboundSimCalleeSimLeg.
            observer = Observer(
                leg_handle.agent_room,
                writer,
                cfg.observe,
                leg_handle.agent_identity,
                leg_handle.sim_identity,
                first_speaker=run.first_speaker,
                recorder=recorder,
            )
            observer.attach()

            # Gemini brain always on sim_room (WebRTC: same as agent_room).
            # Recorder still gets L=sim via mixer; R=agent via Observer (not only Gemini listen path).
            from .caller import DefaultCallerPolicy
            from .caller.policy import CallerPolicyContext
            _midcall_ctx = CallerPolicyContext(
                persona=dict(scenario.persona or {}),
                locale=scenario.effective_locale(),
                context=dict(scenario.context or {}),
                script_steps=list(scenario.script_steps or []),
                first_speaker=run.first_speaker,
            )
            _midcall_cues = DefaultCallerPolicy().midcall_cues(_midcall_ctx)
            _silent = silent_mode_enabled(scenario.persona)
            bridge = GeminiCallerBridge(
                cfg,
                leg_handle.sim_room,
                observer,
                writer,
                persona_system_prompt=scenario.persona_system_prompt(),
                first_speaker=run.first_speaker,
                recorder=recorder,
                voice_gain=resolve_voice_gain(scenario.persona),
                midcall_cues=[] if _silent else _midcall_cues,
                silent_mode=_silent,
            )
            if _silent:
                writer.emit(
                    "sim.silent_mode",
                    spec={
                        "enabled": True,
                        "note": "Caller stays mute: no freestyle, no nudge, no auto barge/noise",
                    },
                    source="sim",
                    include_dialogue=False,
                )
                meta["silent_mode"] = True
            # Listen/record feed derived from SimLegHandle — no mode ifs.
            if leg_handle.gemini_listen_agent_room:
                bridge.watch_agent_tracks_on_room(
                    leg_handle.agent_room, leg_handle.agent_identity
                )
            elif leg_handle.gemini_listen_sip:
                bridge.watch_sip_audio_tracks()
            elif leg_handle.gemini_listen_identity:
                bridge.watch_agent_tracks(leg_handle.gemini_listen_identity)
            else:
                bridge.watch_agent_tracks(leg_handle.agent_identity)

            script_runner: ScriptRunner | None = None
            script_task: asyncio.Task | None = None
            if scenario.script_steps:
                scenario_dir = scenario.path.parent if scenario.path.parent.exists() else cfg.scenarios_dir
                script_runner = ScriptRunner(
                    scenario.script_steps,
                    observer,
                    bridge,
                    writer,
                    scenario_dir=scenario_dir,
                )
                bridge.bind_script_pending(script_runner.has_pending_steps)
                script_task = asyncio.create_task(script_runner.run(), name="script-runner")

            # Parallel interruption-rate policy (#25) — additive to authored Script.
            rate_runner: InterruptRateRunner | None = None
            rate_task: asyncio.Task | None = None
            rate_spec = parse_interrupt_rate(scenario.persona)
            if rate_spec is not None:
                rate_dir = scenario.path.parent if scenario.path.parent.exists() else cfg.scenarios_dir
                rate_runner = InterruptRateRunner(
                    rate_spec,
                    observer,
                    bridge,
                    writer,
                    scenario_dir=rate_dir,
                )
                rate_task = asyncio.create_task(rate_runner.run(), name="interrupt-rate")

            bridge_task = asyncio.create_task(bridge.run(), name="gemini-bridge")
            nudge_task: asyncio.Task | None = None
            if run.first_speaker == "agent" and not scenario.script_steps and not _silent:
                nudge_task = asyncio.create_task(
                    nudge_caller_after_agent_greeting(
                        observer,
                        bridge,
                        writer,
                        first_speaker=run.first_speaker,
                        silent_mode=_silent,
                    ),
                    name="agent-greeted-nudge",
                )
            try:
                end_reason = await _conversation_loop(
                    scenario, run, observer, bridge, writer, cfg.observe.silence_threshold_ms / 1000
                )
            finally:
                if nudge_task is not None:
                    nudge_task.cancel()
                    await asyncio.gather(nudge_task, return_exceptions=True)
                if script_runner is not None:
                    script_runner.stop()
                if script_task is not None:
                    script_task.cancel()
                    await asyncio.gather(script_task, return_exceptions=True)
                if rate_runner is not None:
                    rate_runner.stop()
                if rate_task is not None:
                    rate_task.cancel()
                    await asyncio.gather(rate_task, return_exceptions=True)
                bridge.stop()
                await asyncio.wait_for(asyncio.shield(_settle(bridge_task)), timeout=10)

            writer.emit("run.end_condition", spec={"reason": end_reason}, include_dialogue=False)
            session_snapshot_attempted = True
            await observer.finalize_session_snapshot()
            await observer.detach()
            if leg_handle is not None:
                await leg_handle.disconnect_rooms()
            status = "done"
        except Exception as e:
            writer.emit(
                "run.error",
                spec={"error": f"{type(e).__name__}: {e}", "mode": caller_mode},
                include_dialogue=False,
            )
            status = "failed"
        finally:
            if observer is not None:
                if (
                    not session_snapshot_attempted
                    and not observer.agent_disconnected.is_set()
                ):
                    session_snapshot_attempted = True
                    await observer.finalize_session_snapshot()
                await observer.detach()
            if recorder is not None:
                try:
                    audio_path = report_dir / DEFAULT_FILENAME
                    result = recorder.finalize(audio_path)
                    if result is not None:
                        t0_mono_ms = 0
                        if recorder.started_mono is not None:
                            t0_mono_ms = max(
                                0, int((recorder.started_mono - writer.t0_mono) * 1000)
                            )
                        audio_meta = {
                            "path": str(result.path),
                            "sample_rate": result.sample_rate,
                            "duration_ms": result.duration_ms,
                            "channels": {"left": "sim", "right": "agent"},
                            "sim_samples": result.sim_samples,
                            "agent_samples": result.agent_samples,
                            # Align event ts_mono_ms → audio seconds: audio_ms = ts_mono_ms - t0_mono_ms
                            "t0_mono_ms": t0_mono_ms,
                        }
                        meta["audio"] = audio_meta
                        writer.emit(
                            "sim.audio_recorded",
                            spec=audio_meta,
                            source="sim",
                            include_dialogue=False,
                        )
                    else:
                        writer.emit(
                            "sim.audio_recorded",
                            spec={"path": None, "note": "no audio frames captured"},
                            source="sim",
                            include_dialogue=False,
                        )
                except Exception as e:
                    writer.emit(
                        "sim.error",
                        spec={
                            "where": "audio_finalize",
                            "error": f"{type(e).__name__}: {e}",
                        },
                        source="sim",
                        include_dialogue=False,
                    )
            # Cleanup rooms from SimLegHandle (WebRTC: one room; SIP: agent + sim).
            rooms: list[str] = []
            if leg_handle is not None:
                rooms = list(leg_handle.rooms_to_delete)
                try:
                    await leg_handle.disconnect_rooms()
                except Exception:
                    pass
            elif meta.get("room_name"):
                rooms = [str(meta["room_name"])]
            for rn in dict.fromkeys(rooms):
                await adapter.delete_room(rn)

    # ── Phase: post-run hard verify + report digests ─────────────────────
    summary_extra: dict[str, Any] = {}

    has_script_verify = scenario.script_verify is not None and (
        scenario.script_steps or bool(scenario.script_verify.plugins)
    )
    if status == "done" and has_script_verify:
        script_verify = evaluate_script_log(
            writer.events,
            scenario.script_steps,
            scenario.script_verify,
            scenario=scenario,
            project_root=cfg.project_root,
        )
        writer.emit("script.verify", spec=script_verify, include_dialogue=False)
        summary_extra["script_verify"] = script_verify

    if status == "done" and scenario.asserts is not None and not scenario.asserts.empty:
        from .asserts import evaluate_asserts

        assert_result = evaluate_asserts(writer.events, scenario.asserts)
        writer.emit("assert.verify", spec=assert_result, include_dialogue=False)
        summary_extra["assert_verify"] = assert_result
        if not assert_result.get("pass"):
            # Hard asserts fail the run even if the LLM judge would pass.
            if status == "done":
                status = "failed"
            meta["assert_failed"] = True

    # Caller behavior digest for reports / web (barges, silences, recovery latency).
    if status in ("done", "failed"):
        behavior_summary = build_caller_behavior_summary(writer.events)
        # Enrich recovery latency from assert recovery outcomes when present.
        assert_v = summary_extra.get("assert_verify")
        if isinstance(assert_v, dict):
            for chk in assert_v.get("checks") or []:
                if (
                    isinstance(chk, dict)
                    and chk.get("type") == "recovery"
                    and chk.get("recovery_ms") is not None
                ):
                    behavior_summary["recovery_ms"] = chk.get("recovery_ms")
                    behavior_summary["recovery_assert_pass"] = bool(chk.get("pass"))
                    break
        summary_extra["caller"] = {"behavior_summary": behavior_summary}

    # ── Phase: soft LLM judge (does not flip hard gate by itself) ────────
    if status in ("done", "failed") and cfg.judge is not None and scenario.pass_criteria:
        try:
            tool_events = [e for e in writer.events if e["kind"].startswith("tool.")]
            # Include llm_bool outcome prompts as extra criteria when present.
            criteria = list(scenario.pass_criteria)
            if scenario.asserts:
                for oc in scenario.asserts.outcomes:
                    if oc.type == "llm_bool" and oc.prompt:
                        criteria.append(f"[outcome:{oc.id}] {oc.prompt}")
            from .evals.runner import judge_run, judge_run_multi

            if getattr(scenario, "pass_judges", None):
                verdict = await judge_run_multi(
                    cfg.judge,
                    cfg.simulator.google_api_key,
                    scenario.pass_judges,
                    getattr(scenario, "pass_criteria_mode", None) or "all",
                    writer.turn_metrics(),
                    tool_events,
                )
            else:
                verdict = await judge_run(
                    cfg.judge,
                    cfg.simulator.google_api_key,
                    criteria,
                    writer.turn_metrics(),
                    tool_events,
                )
        except Exception as e:
            verdict = {
                "verdict": "error",
                "notes": f"Judge failed (soft): {type(e).__name__}: {e}",
            }
        writer.emit("judge.verdict", spec=verdict or {}, include_dialogue=False)

    # ── Post-run: goals_met (hard fail only on explicit LLM fail; soft-skip if judge unavailable) ─
    if status in ("done", "failed") and scenario.asserts and cfg.judge is not None:
        from .evals.runner import judge_goals

        for oc in scenario.asserts.outcomes or []:
            if oc.type != "goals_met":
                continue
            goal_list = list(oc.goals) if oc.goals else [
                g for g in (scenario.persona.get("goals") or []) if isinstance(g, str)
            ]
            if not goal_list:
                continue
            try:
                goals_result = await judge_goals(
                    cfg.judge, cfg.simulator.google_api_key,
                    goal_list, oc.min_goals,
                    writer.turn_metrics(),
                )
                gv = str((goals_result or {}).get("verdict") or "fail").lower()
                notes = str((goals_result or {}).get("notes") or "")
                # Misconfig / transport / skip → do not flip hard run status
                if gv in ("skipped", "error"):
                    writer.emit(
                        "assert.goals_met",
                        spec={
                            "outcome_id": oc.id,
                            "min_goals": oc.min_goals,
                            "goals": goal_list,
                            "verdict": gv,
                            "pass": True,
                            "skipped": True,
                            "notes": notes or "goals_met soft-skipped (judge unavailable).",
                        },
                        include_dialogue=False,
                    )
                    continue
                try:
                    gs = int((goals_result or {}).get("score", 0))
                except (TypeError, ValueError):
                    gs = 0
                goals_pass = gv == "pass" and gs >= 50
                writer.emit(
                    "assert.goals_met",
                    spec={
                        "outcome_id": oc.id,
                        "min_goals": oc.min_goals,
                        "goals": goal_list,
                        "verdict": gv,
                        "score": gs,
                        "pass": goals_pass,
                        "notes": notes,
                    },
                    include_dialogue=False,
                )
                if not goals_pass:
                    if status == "done":
                        status = "failed"
                    meta.setdefault("goals_failed", []).append(oc.id)
            except Exception as e:
                writer.emit(
                    "assert.goals_met",
                    spec={
                        "outcome_id": oc.id,
                        "error": f"{type(e).__name__}: {e}",
                        "pass": True,
                        "skipped": True,
                        "notes": "goals_met soft-skipped after judge exception.",
                    },
                    include_dialogue=False,
                )

    summary = writer.finalize(status, meta=meta, verdict=verdict)
    summary.setdefault("caller_mode", caller_mode)
    if meta.get("dial_ms") is not None:
        summary.setdefault("dial_ms", meta.get("dial_ms"))
    if summary_extra:
        summary.update(summary_extra)
        (report_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        # Persist mode/dial fields even without assert/script extras.
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

        # During scripted user silence (+ grace), do not kill the call as dead_call.
        # Real agents may wait while the "caller" is intentionally quiet for N seconds.
        scripted_hold = False
        if hasattr(bridge, "scripted_silence_active"):
            try:
                scripted_hold = bool(bridge.scripted_silence_active())
            except Exception:  # noqa: BLE001
                scripted_hold = False

        silent_for = time.monotonic() - observer.last_agent_activity_mono
        if silent_for >= cfg_silence_s:
            if silence_reported_at is None or (time.monotonic() - silence_reported_at) >= cfg_silence_s:
                writer.emit(
                    "silence.detected",
                    spec={
                        "duration_ms": int(silent_for * 1000),
                        "scripted_user_silence": scripted_hold,
                    },
                    source="observer",
                )
                silence_reported_at = time.monotonic()
            if silent_for >= cfg_silence_s * 3 and not scripted_hold:
                return "dead_call_silence"
        else:
            silence_reported_at = None

        await asyncio.sleep(0.25)
