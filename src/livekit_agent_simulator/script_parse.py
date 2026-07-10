from __future__ import annotations

from typing import Any

from .script_runner import ScriptStep, ScriptVerifySpec


def parse_script_verify(raw: Any) -> ScriptVerifySpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Script.spec.verify must be an object")
    return ScriptVerifySpec(
        require_during_agent_speech=bool(raw.get("require_during_agent_speech", True)),
        min_agent_finals_after_first_cue=int(raw.get("min_agent_finals_after_first_cue", 0)),
        min_user_finals_after_first_cue=int(raw.get("min_user_finals_after_first_cue", 0)),
        min_interruptions=int(raw["min_interruptions"])
        if raw.get("min_interruptions") is not None
        else None,
        max_interruptions=int(raw["max_interruptions"])
        if raw.get("max_interruptions") is not None
        else None,
    )


def parse_script_steps(spec: dict[str, Any], path_label: str) -> list[ScriptStep]:
    raw_steps = spec.get("steps")
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        raise ValueError(f"{path_label}: Script.spec.steps must be an array")

    steps: list[ScriptStep] = []
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: Script.spec.steps[{i}] must be an object")
        step_id = str(raw.get("id") or raw.get("label") or f"step-{i}")
        trigger = str(raw.get("trigger", "agent_speaking"))
        if trigger != "agent_speaking":
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: unsupported trigger {trigger!r} "
                "(supported: agent_speaking)"
            )
        say = raw.get("say") or raw.get("text")
        if not say or not str(say).strip():
            raise ValueError(f"{path_label}: Script step {step_id!r}: say/text is required")
        delivery = str(raw.get("delivery", "gemini_text"))
        if delivery not in ("gemini_text", "room_pcm"):
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: delivery must be gemini_text or room_pcm"
            )
        asset = raw.get("asset")
        if delivery == "room_pcm" and not asset:
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: room_pcm delivery requires asset (WAV path)"
            )
        steps.append(
            ScriptStep(
                id=step_id,
                trigger=trigger,
                delay_ms=int(raw.get("delay_ms", 800)),
                say=str(say).strip(),
                label=str(raw.get("label") or step_id),
                once=bool(raw.get("once", True)),
                min_agent_active_ms=int(raw.get("min_agent_active_ms", 400)),
                delivery=delivery,
                asset=str(asset).strip() if asset else None,
            )
        )
    return steps
