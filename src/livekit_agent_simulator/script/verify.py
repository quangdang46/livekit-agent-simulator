"""Log-based script verify (hard pass/fail without LLM judge)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ScriptStep, ScriptVerifySpec

def evaluate_script_log(
    events: list[dict],
    steps: list[ScriptStep],
    verify: ScriptVerifySpec | None = None,
    *,
    scenario: Any | None = None,
    project_root: Path | str | None = None,
) -> dict[str, object]:
    """Log-based PASS/FAIL for scripted adaptive scenarios (no LLM judge required)."""
    cues = [e for e in events if e.get("kind") in ("sim.script.cue", "sim.script.wait")]
    agent_finals = [e for e in events if e.get("kind") == "transcript.agent.final"]
    user_finals = [e for e in events if e.get("kind") == "transcript.user.final"]
    interruptions = [e for e in events if e.get("kind") == "interruption"]

    checks: list[dict[str, object]] = []

    for step in steps:
        matching = [c for c in cues if c.get("spec", {}).get("step_id") == step.id]
        if not matching:
            checks.append({"step_id": step.id, "pass": False, "reason": "script step not fired"})
            continue
        cue = matching[0]
        spec = cue.get("spec") or {}
        during = bool(spec.get("during_agent_speech"))
        if step.trigger == "agent_speaking" and step.action == "speak" and not during:
            checks.append(
                {
                    "step_id": step.id,
                    "pass": False,
                    "reason": "cue fired but agent was not active speaker",
                }
            )
            continue
        checks.append(
            {
                "step_id": step.id,
                "pass": True,
                "during_agent_speech": during,
                "trigger": step.trigger,
                "action": step.action,
            }
        )

    cue_ms = cues[0]["ts_mono_ms"] if cues else None
    silence_cues = [
        e
        for e in cues
        if (e.get("spec") or {}).get("trigger") == "silence"
        or (e.get("spec") or {}).get("action") == "wait"
    ]
    silence_ms = silence_cues[0]["ts_mono_ms"] if silence_cues else None
    from .models import counts_for_recovery_barge

    barge_cues = []
    for e in cues:
        if e.get("kind") != "sim.script.cue":
            continue
        spec = e.get("spec") or {}
        cls = spec.get("class") or spec.get("interrupt_class")
        cls_s = str(cls) if cls else None
        if counts_for_recovery_barge(
            barge_in=bool(spec.get("barge_in")), interrupt_class=cls_s
        ):
            barge_cues.append(e)
            continue
        # Legacy heuristic: short during-agent cue without class (treat as correction)
        if (
            not cls_s
            and spec.get("during_agent_speech")
            and spec.get("trigger") == "agent_speaking"
            and int(spec.get("waited_ms") or 9999) < 800
        ):
            barge_cues.append(e)
    barge_ms = barge_cues[0]["ts_mono_ms"] if barge_cues else None

    agent_after_cue = (
        sum(1 for e in agent_finals if cue_ms is not None and e.get("ts_mono_ms", 0) >= cue_ms)
        if cue_ms is not None
        else 0
    )
    user_after_cue = (
        sum(1 for e in user_finals if cue_ms is not None and e.get("ts_mono_ms", 0) >= cue_ms)
        if cue_ms is not None
        else 0
    )
    agent_after_silence = (
        sum(
            1
            for e in agent_finals
            if silence_ms is not None and e.get("ts_mono_ms", 0) >= silence_ms
        )
        if silence_ms is not None
        else 0
    )
    agent_after_barge = (
        sum(
            1
            for e in agent_finals
            if barge_ms is not None and e.get("ts_mono_ms", 0) > barge_ms
        )
        if barge_ms is not None
        else 0
    )

    verify = verify or ScriptVerifySpec()
    if verify.min_agent_finals_after_first_cue > 0:
        ok = agent_after_cue >= verify.min_agent_finals_after_first_cue
        checks.append(
            {
                "check": "min_agent_finals_after_first_cue",
                "pass": ok,
                "expected": verify.min_agent_finals_after_first_cue,
                "actual": agent_after_cue,
            }
        )
    if verify.min_user_finals_after_first_cue > 0:
        ok = user_after_cue >= verify.min_user_finals_after_first_cue
        checks.append(
            {
                "check": "min_user_finals_after_first_cue",
                "pass": ok,
                "expected": verify.min_user_finals_after_first_cue,
                "actual": user_after_cue,
            }
        )
    if verify.min_agent_finals_after_silence > 0:
        ok = agent_after_silence >= verify.min_agent_finals_after_silence
        checks.append(
            {
                "check": "min_agent_finals_after_silence",
                "pass": ok,
                "expected": verify.min_agent_finals_after_silence,
                "actual": agent_after_silence,
            }
        )
    if verify.min_agent_finals_after_barge_in > 0:
        ok = agent_after_barge >= verify.min_agent_finals_after_barge_in
        checks.append(
            {
                "check": "min_agent_finals_after_barge_in",
                "pass": ok,
                "expected": verify.min_agent_finals_after_barge_in,
                "actual": agent_after_barge,
            }
        )
    if verify.min_interruptions is not None:
        ok = len(interruptions) >= verify.min_interruptions
        checks.append(
            {
                "check": "min_interruptions",
                "pass": ok,
                "expected": verify.min_interruptions,
                "actual": len(interruptions),
            }
        )
    if verify.max_interruptions is not None:
        ok = len(interruptions) <= verify.max_interruptions
        checks.append(
            {
                "check": "max_interruptions",
                "pass": ok,
                "expected": verify.max_interruptions,
                "actual": len(interruptions),
            }
        )

    plugin_results: list[dict[str, object]] = []
    if verify.plugins:
        from ..plugins.api import VerifyContext
        from ..plugins.loader import ensure_plugins_loaded
        from ..plugins.registry import get_verify

        if project_root is not None:
            ensure_plugins_loaded(
                project_root,
                list(scenario.plugin_modules) if scenario is not None else None,
            )
        for plugin_name in verify.plugins:
            fn = get_verify(plugin_name)
            if fn is None:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": f"verify plugin {plugin_name!r} is not registered",
                    }
                )
                continue
            if scenario is None or project_root is None:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": "plugin verify requires scenario and project_root",
                    }
                )
                continue
            opts = verify.plugin_options.get(plugin_name, {})
            if not isinstance(opts, dict):
                opts = {}
            ctx = VerifyContext(
                events=events,
                steps=steps,
                verify=verify,
                scenario=scenario,
                project_root=Path(project_root),
                plugin_name=plugin_name,
                options=dict(opts),
            )
            try:
                raw = fn(ctx)
            except Exception as e:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": f"{type(e).__name__}: {e}",
                    }
                )
                continue
            passed = bool(raw.get("pass"))
            plugin_checks = raw.get("checks")
            if isinstance(plugin_checks, list):
                for item in plugin_checks:
                    if isinstance(item, dict):
                        checks.append({**item, "plugin": plugin_name})
            checks.append(
                {
                    "check": f"plugin:{plugin_name}",
                    "pass": passed,
                    "plugin": plugin_name,
                    "detail": raw.get("detail"),
                }
            )
            plugin_results.append({"plugin": plugin_name, "pass": passed, "result": raw})

    return {
        "script_steps": len(steps),
        "cues_fired": len([e for e in cues if e.get("kind") == "sim.script.cue"]),
        "waits_fired": len([e for e in cues if e.get("kind") == "sim.script.wait"]),
        "agent_finals_after_first_cue": agent_after_cue,
        "user_finals_after_first_cue": user_after_cue,
        "agent_finals_after_silence": agent_after_silence,
        "agent_finals_after_barge_in": agent_after_barge,
        "interruptions": len(interruptions),
        "checks": checks,
        "plugin_results": plugin_results,
        "pass": all(bool(c.get("pass")) for c in checks) if checks else False,
    }
