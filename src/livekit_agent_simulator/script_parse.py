from __future__ import annotations

from typing import Any

from .script import (
    SUPPORTED_ACTIONS,
    SUPPORTED_TRIGGERS,
    ScriptStep,
    ScriptVerifySpec,
    normalize_interrupt_class,
)


def _parse_step_gain(raw: dict[str, Any], path_label: str, step_id: str) -> float:
    key = "gain"
    if "gain" not in raw and "volume" in raw:
        key = "volume"
    if key not in raw:
        return 1.0
    try:
        gain = float(raw[key])
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"{path_label}: Script step {step_id!r}: {key} must be a number"
        ) from e
    if not 0.0 <= gain <= 1.0:
        raise ValueError(
            f"{path_label}: Script step {step_id!r}: {key} must be between 0.0 and 1.0"
        )
    return gain


def parse_script_verify(raw: Any) -> ScriptVerifySpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Script.spec.verify must be an object")
    plugins_raw = raw.get("plugins")
    if plugins_raw is None and raw.get("plugin"):
        plugins_raw = [raw.get("plugin")]
    plugins: tuple[str, ...] = ()
    if plugins_raw is not None:
        if not isinstance(plugins_raw, list):
            raise ValueError("Script.spec.verify.plugins must be an array of plugin names")
        plugins = tuple(str(p).strip() for p in plugins_raw if str(p).strip())

    options_raw = raw.get("plugin_options")
    if options_raw is not None and not isinstance(options_raw, dict):
        raise ValueError("Script.spec.verify.plugin_options must be an object")
    plugin_options = dict(options_raw) if isinstance(options_raw, dict) else {}

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
        min_agent_finals_after_silence=int(raw.get("min_agent_finals_after_silence", 0)),
        min_agent_finals_after_barge_in=int(raw.get("min_agent_finals_after_barge_in", 0)),
        plugins=plugins,
        plugin_options=plugin_options,
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
        if trigger not in SUPPORTED_TRIGGERS:
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: unsupported trigger {trigger!r} "
                f"(supported: {sorted(SUPPORTED_TRIGGERS)})"
            )
        action = str(raw.get("action", "speak"))
        if action not in SUPPORTED_ACTIONS:
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: action must be speak|wait"
            )
        say = raw.get("say") or raw.get("text") or ""
        if action == "speak" and not str(say).strip():
            raise ValueError(f"{path_label}: Script step {step_id!r}: say/text required when action=speak")
        delivery = str(raw.get("delivery", "gemini_text"))
        if delivery not in ("gemini_text", "room_pcm"):
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: delivery must be gemini_text or room_pcm"
            )
        asset = raw.get("asset")
        if action == "speak" and delivery == "room_pcm" and not asset:
            raise ValueError(
                f"{path_label}: Script step {step_id!r}: room_pcm delivery requires asset (WAV path)"
            )
        # Barge-in convenience: short delay while agent speaking
        delay_ms = int(raw.get("delay_ms", 800))
        min_agent = int(raw.get("min_agent_active_ms", 400))
        barge_in = bool(raw.get("barge_in") or raw.get("interrupt"))
        if barge_in:
            delay_ms = int(raw.get("delay_ms", 250))
            min_agent = int(raw.get("min_agent_active_ms", 200))
            trigger = "agent_speaking"
            action = "speak"
        # Default: blip on text barge; off when room_pcm (asset is the cut-in audio).
        if "with_blip" in raw:
            with_blip = bool(raw.get("with_blip"))
        else:
            with_blip = barge_in and delivery != "room_pcm"
        gain = _parse_step_gain(raw, path_label, step_id)
        try:
            interrupt_class = normalize_interrupt_class(
                raw.get("class") or raw.get("interrupt_class"),
                barge_in=barge_in,
            )
        except ValueError as e:
            raise ValueError(f"{path_label}: Script step {step_id!r}: {e}") from e

        steps.append(
            ScriptStep(
                id=step_id,
                trigger=trigger,
                delay_ms=delay_ms,
                say=str(say).strip(),
                label=str(raw.get("label") or step_id),
                once=bool(raw.get("once", True)),
                min_agent_active_ms=min_agent,
                delivery=delivery,
                asset=str(asset).strip() if asset else None,
                silence_after_cue_ms=int(raw.get("silence_after_cue_ms", 0)),
                action=action,
                require_agent_spoke_first=bool(raw.get("require_agent_spoke_first", True)),
                barge_in=barge_in,
                with_blip=with_blip,
                gain=gain,
                interrupt_class=interrupt_class,
            )
        )
    return steps
