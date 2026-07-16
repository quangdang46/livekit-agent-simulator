"""Build a Scenario from a dict (dynamic API — no JSONL file required)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .asserts import parse_assert_spec
from .scenario import (
    API_VERSION,
    CALLER_MODES,
    CallerSpec,
    DispatchSpec,
    ExecuteSpec,
    Scenario,
    ScenarioError,
    SimulatorSpec,
    TelephonySpec,
)
from .script_parse import parse_script_steps, parse_script_verify


def scenario_from_dict(
    data: dict[str, Any],
    *,
    path: Path | None = None,
    path_label: str = "scenario_dict",
) -> Scenario:
    """Parse the same shape as export_scenario / JSONL sections (nested dict)."""
    metadata = data.get("metadata") or {}
    scenario_id = data.get("id") or metadata.get("id")
    if not scenario_id:
        raise ScenarioError(f"{path_label}: id or metadata.id is required")

    sim_raw = data.get("simulator") or {}
    simulator = SimulatorSpec(
        max_turns=int(sim_raw.get("max_turns", 6)),
        timeout_s=int(sim_raw.get("timeout_s", 120)),
        first_speaker=str(sim_raw.get("first_speaker", "agent")),
    )

    execute: ExecuteSpec | None = None
    ex_raw = data.get("execute")
    if isinstance(ex_raw, dict):
        execute = ExecuteSpec(
            max_turns=int(ex_raw["max_turns"]) if ex_raw.get("max_turns") is not None else None,
            timeout_s=int(ex_raw["timeout_s"]) if ex_raw.get("timeout_s") is not None else None,
            first_speaker=str(ex_raw["first_speaker"]) if ex_raw.get("first_speaker") else None,
        )

    run_raw = data.get("run")
    if isinstance(run_raw, dict) and execute is None:
        execute = ExecuteSpec(
            max_turns=int(run_raw["max_turns"]) if run_raw.get("max_turns") is not None else None,
            timeout_s=int(run_raw["timeout_s"]) if run_raw.get("timeout_s") is not None else None,
            first_speaker=str(run_raw["first_speaker"]) if run_raw.get("first_speaker") else None,
        )

    dispatch: DispatchSpec | None = None
    disp_raw = data.get("dispatch")
    if isinstance(disp_raw, dict):
        meta = disp_raw.get("metadata")
        if meta is not None and str(meta).strip():
            dispatch = DispatchSpec(metadata=str(meta).strip())

    caller: CallerSpec | None = None
    caller_raw = data.get("caller")
    if isinstance(caller_raw, dict) and caller_raw.get("mode"):
        mode = str(caller_raw["mode"]).strip().lower()
        if mode not in CALLER_MODES:
            raise ScenarioError(
                f"{path_label}: caller.mode must be one of {sorted(CALLER_MODES)} (got {mode!r})"
            )
        caller = CallerSpec(mode=mode)

    telephony: TelephonySpec | None = None
    tel_raw = data.get("telephony")
    if isinstance(tel_raw, dict):
        def _opt(key: str) -> str | None:
            v = tel_raw.get(key)
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        prepare = tel_raw.get("prepare_ms")
        wait = tel_raw.get("wait_until_answered")
        krisp = tel_raw.get("krisp_enabled")
        handset_iso = _opt("handset_isolation")
        telephony = TelephonySpec(
            call_to=_opt("call_to"),
            dial_in=_opt("dial_in"),
            sip_trunk_id=_opt("sip_trunk_id") or _opt("outbound_trunk_id"),
            prepare_ms=int(prepare) if prepare is not None else None,
            wait_until_answered=bool(wait) if wait is not None else None,
            krisp_enabled=bool(krisp) if krisp is not None else None,
            agent_room=_opt("agent_room"),
            agent_room_name_template=_opt("agent_room_name_template"),
            handset_isolation=handset_iso,
        )

    script_steps = []
    script_verify = None
    script_raw = data.get("script")
    if isinstance(script_raw, dict):
        script_steps = parse_script_steps(script_raw, path_label)
        script_verify = parse_script_verify(script_raw.get("verify"))

    persona = dict(data.get("persona") or {})
    if not persona.get("brief"):
        raise ScenarioError(f"{path_label}: persona.brief is required")

    plugin_modules = [str(m) for m in (data.get("plugin_modules") or data.get("plugins") or [])]

    asserts = None
    assert_raw = data.get("assert") or data.get("asserts")
    if isinstance(assert_raw, dict):
        try:
            asserts = parse_assert_spec(assert_raw, path_label)
        except ValueError as e:
            raise ScenarioError(str(e)) from e

    behavior_spec = None
    beh_raw = data.get("behavior")
    if isinstance(beh_raw, dict):
        behavior_spec = dict(beh_raw)

    scenario = Scenario(
        id=str(scenario_id),
        path=path or Path(f"{scenario_id}.jsonl"),
        locale=str(data.get("locale") or metadata.get("locale", "en-US")),
        tags=[str(t) for t in (data.get("tags") or metadata.get("tags") or [])],
        persona=persona,
        context=dict(data.get("context") or {}),
        simulator=simulator,
        execute=execute,
        dispatch=dispatch,
        caller=caller,
        telephony=telephony,
        pass_criteria=[str(c) for c in (data.get("pass_criteria") or [])],
        script_steps=script_steps,
        script_verify=script_verify,
        plugin_modules=plugin_modules,
        asserts=asserts,
        behavior_spec=behavior_spec,
    )

    try:
        from .behavior_compile import apply_caller_behavior

        scenario.script_steps, scenario.script_verify = apply_caller_behavior(
            scenario.persona,
            scenario.behavior_spec,
            scenario.script_steps,
            scenario.script_verify,
            path_label=path_label,
        )
    except ValueError as e:
        raise ScenarioError(str(e)) from e

    if scenario.simulator.first_speaker not in ("agent", "user"):
        raise ScenarioError(f"{path_label}: simulator.first_speaker must be agent or user")
    if scenario.run_spec.first_speaker not in ("agent", "user"):
        raise ScenarioError(f"{path_label}: execute.first_speaker must be agent or user")
    if dispatch and dispatch.metadata:
        import json

        try:
            json.loads(dispatch.metadata)
        except json.JSONDecodeError as e:
            raise ScenarioError(f"{path_label}: dispatch.metadata must be valid JSON — {e}") from e

    return scenario


def export_scenario_dict(scenario: Scenario) -> dict[str, Any]:
    """Full structured export including script steps (for dev tooling)."""
    base = scenario.export_dict()
    base["persona"] = scenario.persona
    base["context"] = scenario.context
    base["plugin_modules"] = list(scenario.plugin_modules)
    if scenario.dispatch and scenario.dispatch.metadata:
        base["dispatch"] = {"metadata": scenario.dispatch.metadata}
    if scenario.script_steps:
        base["script"] = {
            "steps": [
                {
                    "id": s.id,
                    "trigger": s.trigger,
                    "delay_ms": s.delay_ms,
                    "say": s.say,
                    "label": s.label,
                    "once": s.once,
                    "min_agent_active_ms": s.min_agent_active_ms,
                    "delivery": s.delivery,
                    "asset": s.asset,
                    "silence_after_cue_ms": s.silence_after_cue_ms,
                    "barge_in": s.barge_in,
                    "class": s.interrupt_class,
                    "with_blip": s.with_blip,
                    "gain": s.gain,
                    "loop": s.loop,
                }
                for s in scenario.script_steps
            ],
            "verify": None
            if scenario.script_verify is None
            else {
                "require_during_agent_speech": scenario.script_verify.require_during_agent_speech,
                "min_agent_finals_after_first_cue": scenario.script_verify.min_agent_finals_after_first_cue,
                "min_user_finals_after_first_cue": scenario.script_verify.min_user_finals_after_first_cue,
                "min_interruptions": scenario.script_verify.min_interruptions,
                "max_interruptions": scenario.script_verify.max_interruptions,
                "plugins": list(scenario.script_verify.plugins),
                "plugin_options": dict(scenario.script_verify.plugin_options),
            },
        }
    return base
