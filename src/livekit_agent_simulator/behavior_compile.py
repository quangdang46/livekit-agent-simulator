"""Compile Hamming-style Behavior / speech_conditions into ScriptStep lists.

Explicit Script steps win by id; compiled steps fill gaps (append unknown ids).
"""

from __future__ import annotations

from typing import Any

from .script import ScriptStep, ScriptVerifySpec, normalize_interrupt_class


def _is_voice_asset(asset: str | None) -> bool:
    """True for package vocal speech refs (voice.*), not synthetic noise.*."""
    if not asset:
        return False
    name = str(asset).strip().lower()
    if name.startswith("builtin:"):
        name = name[len("builtin:") :]
    if name.startswith("@"):
        name = name[1:]
    return name.startswith("voice.")


def _norm_constraints(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def speech_conditions_of(persona: dict[str, Any]) -> dict[str, Any]:
    sc = persona.get("speech_conditions") or persona.get("speechConditions") or {}
    return sc if isinstance(sc, dict) else {}


def compile_from_speech_conditions(persona: dict[str, Any]) -> list[ScriptStep]:
    """Derive default timed steps from Persona.speech_conditions."""
    sc = speech_conditions_of(persona)
    if not sc:
        return []
    steps: list[ScriptStep] = []

    noise = sc.get("noise") or sc.get("ambient")
    if noise:
        delay = int(sc.get("noise_delay_ms") or sc.get("after_join_ms") or 5000)
        noise_gain = float(sc.get("noise_gain", 1.0))
        if not 0.0 <= noise_gain <= 1.0:
            raise ValueError("Persona.speech_conditions.noise_gain must be between 0.0 and 1.0")
        steps.append(
            ScriptStep(
                id="auto-ambient",
                trigger="time",
                delay_ms=max(0, delay),
                say="[ambient]",
                label="auto-ambient",
                delivery="room_pcm",
                asset=str(noise).strip(),
                once=True,
                gain=noise_gain,
            )
        )

    barge = str(sc.get("barge_policy") or sc.get("barge") or "").strip().lower()
    if barge in ("mid_agent_turn", "mid", "interrupt", "barge", "true", "1"):
        after = int(sc.get("barge_after_agent_ms") or sc.get("after_agent_ms") or 600)
        say = str(sc.get("barge_say") or "Sorry — one second —").strip()
        asset = sc.get("barge_asset")
        asset_s = str(asset).strip() if asset else ""
        delivery = "room_pcm" if asset_s else "gemini_text"
        # Text barge: blip by default. Vocal WAV (voice.*) already carries energy — no blip.
        default_blip = not _is_voice_asset(asset_s) if asset_s else True
        with_blip = bool(sc.get("with_blip", default_blip))
        barge_gain = float(sc.get("barge_gain", sc.get("gain", 1.0)))
        if not 0.0 <= barge_gain <= 1.0:
            raise ValueError("Persona.speech_conditions.barge_gain must be between 0.0 and 1.0")
        barge_class = normalize_interrupt_class(
            sc.get("barge_class") or sc.get("class") or "correction",
            barge_in=True,
        )
        steps.append(
            ScriptStep(
                id="auto-barge-1",
                trigger="agent_speaking",
                delay_ms=max(100, after // 2),
                say=say if delivery == "gemini_text" else (say or "[barge]"),
                label="auto-barge-1",
                min_agent_active_ms=max(100, after // 2),
                delivery=delivery,
                asset=asset_s or None,
                barge_in=True,
                with_blip=with_blip,
                once=True,
                gain=barge_gain,
                interrupt_class=barge_class,
            )
        )

    silence_ms = int(sc.get("silence_ms") or sc.get("user_silence_ms") or 0)
    if silence_ms >= 500:
        steps.append(
            ScriptStep(
                id="auto-user-silence",
                trigger="time",
                delay_ms=int(sc.get("silence_arm_ms") or 400),
                say="",
                label="auto-user-silence",
                action="wait",
                silence_after_cue_ms=silence_ms,
                once=True,
            )
        )

    return steps


def compile_from_behavior_spec(spec: dict[str, Any], path_label: str = "Behavior") -> list[ScriptStep]:
    """Expand kind=Behavior.spec into ScriptStep list."""
    if not isinstance(spec, dict):
        raise ValueError(f"{path_label}: spec must be an object")
    steps: list[ScriptStep] = []

    ambient = spec.get("ambient")
    if isinstance(ambient, dict) and ambient.get("asset"):
        delay = int(ambient.get("delay_ms") or 5000)
        steps.append(
            ScriptStep(
                id=str(ambient.get("id") or "behavior-ambient"),
                trigger="time",
                delay_ms=max(0, delay),
                say=str(ambient.get("say") or "[ambient]"),
                label=str(ambient.get("label") or "behavior-ambient"),
                delivery="room_pcm",
                asset=str(ambient["asset"]).strip(),
                once=bool(ambient.get("once", True)),
            )
        )

    for i, raw in enumerate(spec.get("barge_ins") or spec.get("barge_in") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: barge_ins[{i}] must be object")
        sid = str(raw.get("id") or f"behavior-barge-{i}")
        after = int(raw.get("after_agent_ms") or raw.get("delay_ms") or 600)
        say = str(raw.get("say") or raw.get("text") or "Wait —").strip()
        asset = raw.get("asset")
        asset_s = str(asset).strip() if asset else ""
        # Asset → room_pcm unless delivery is explicitly set.
        if "delivery" in raw:
            delivery = str(raw.get("delivery") or "gemini_text")
        else:
            delivery = "room_pcm" if asset_s else "gemini_text"
        if delivery == "room_pcm" and not asset_s:
            raise ValueError(f"{path_label}: barge_ins[{i}] room_pcm needs asset")
        # Vocal WAV already audible; noise room_pcm / gemini_text keep blip defaults.
        if "with_blip" in raw:
            with_blip = bool(raw.get("with_blip"))
        elif _is_voice_asset(asset_s):
            with_blip = False
        else:
            with_blip = delivery != "room_pcm"
        step_gain = float(raw.get("gain", raw.get("volume", 1.0)))
        if not 0.0 <= step_gain <= 1.0:
            raise ValueError(f"{path_label}: barge_ins[{i}] gain must be between 0.0 and 1.0")
        try:
            icls = normalize_interrupt_class(
                raw.get("class") or raw.get("interrupt_class") or "correction",
                barge_in=True,
            )
        except ValueError as e:
            raise ValueError(f"{path_label}: barge_ins[{i}]: {e}") from e
        steps.append(
            ScriptStep(
                id=sid,
                trigger="agent_speaking",
                delay_ms=max(100, int(raw.get("delay_ms") or max(150, after // 2))),
                say=say,
                label=str(raw.get("label") or sid),
                min_agent_active_ms=max(100, int(raw.get("min_agent_active_ms") or max(150, after // 2))),
                delivery=delivery,
                asset=asset_s or None,
                barge_in=True,
                with_blip=with_blip,
                once=bool(raw.get("once", True)),
                gain=step_gain,
                interrupt_class=icls,
            )
        )

    for i, raw in enumerate(spec.get("backchannels") or spec.get("backchannel") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: backchannels[{i}] must be object")
        sid = str(raw.get("id") or f"behavior-backchannel-{i}")
        after = int(raw.get("after_agent_ms") or raw.get("delay_ms") or 1200)
        say = str(raw.get("say") or raw.get("text") or "uh-huh").strip()
        asset = raw.get("asset") or "builtin:voice.backchannel"
        asset_s = str(asset).strip()
        step_gain = float(raw.get("gain", raw.get("volume", 1.0)))
        if not 0.0 <= step_gain <= 1.0:
            raise ValueError(f"{path_label}: backchannels[{i}] gain must be between 0.0 and 1.0")
        steps.append(
            ScriptStep(
                id=sid,
                trigger="agent_speaking",
                delay_ms=max(100, after),
                say=say,
                label=str(raw.get("label") or sid),
                min_agent_active_ms=max(100, int(raw.get("min_agent_active_ms") or after)),
                delivery="room_pcm",
                asset=asset_s,
                barge_in=False,
                with_blip=False,
                once=bool(raw.get("once", True)),
                gain=step_gain,
                interrupt_class="backchannel",
            )
        )

    for i, raw in enumerate(spec.get("false_interrupts") or spec.get("false_interrupt") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: false_interrupts[{i}] must be object")
        sid = str(raw.get("id") or f"behavior-noise-{i}")
        after = int(raw.get("after_agent_ms") or raw.get("delay_ms") or 500)
        say = str(raw.get("say") or raw.get("text") or "[noise]").strip()
        asset = raw.get("asset") or "builtin:noise.loud"
        asset_s = str(asset).strip()
        step_gain = float(raw.get("gain", raw.get("volume", 1.0)))
        if not 0.0 <= step_gain <= 1.0:
            raise ValueError(f"{path_label}: false_interrupts[{i}] gain must be between 0.0 and 1.0")
        # barge_in True so it fires during agent speech + optional blip energy,
        # but class=noise excludes it from recovery metrics.
        steps.append(
            ScriptStep(
                id=sid,
                trigger="agent_speaking",
                delay_ms=max(100, after),
                say=say,
                label=str(raw.get("label") or sid),
                min_agent_active_ms=max(100, int(raw.get("min_agent_active_ms") or after)),
                delivery="room_pcm",
                asset=asset_s,
                barge_in=True,
                with_blip=bool(raw.get("with_blip", False)),
                once=bool(raw.get("once", True)),
                gain=step_gain,
                interrupt_class="noise",
            )
        )

    for i, raw in enumerate(spec.get("user_silence") or spec.get("silences") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: user_silence[{i}] must be object")
        sid = str(raw.get("id") or f"behavior-silence-{i}")
        hold = int(raw.get("hold_ms") or raw.get("silence_after_cue_ms") or 0)
        if hold < 500:
            raise ValueError(f"{path_label}: user_silence[{i}] hold_ms must be >= 500")
        arm = int(raw.get("delay_ms") or raw.get("arm_ms") or 400)
        steps.append(
            ScriptStep(
                id=sid,
                trigger=str(raw.get("trigger") or "time"),
                delay_ms=max(0, arm),
                say="",
                label=str(raw.get("label") or sid),
                action="wait",
                silence_after_cue_ms=hold,
                require_agent_spoke_first=bool(raw.get("require_agent_spoke_first", True)),
                once=bool(raw.get("once", True)),
            )
        )

    return steps


def merge_script_steps(
    explicit: list[ScriptStep],
    compiled: list[ScriptStep],
) -> list[ScriptStep]:
    """Explicit Script wins on id collision; compiled steps append if id free."""
    if not compiled:
        return list(explicit)
    if not explicit:
        return list(compiled)
    seen = {s.id for s in explicit}
    out = list(explicit)
    for s in compiled:
        if s.id in seen:
            continue
        out.append(s)
        seen.add(s.id)
    return out


def default_verify_for_compiled(
    steps: list[ScriptStep],
    existing: ScriptVerifySpec | None,
) -> ScriptVerifySpec | None:
    """If we auto-added barge/silence and no verify, soft defaults for recovery."""
    if existing is not None:
        return existing
    from .script.models import counts_for_recovery_barge

    has_barge = any(
        counts_for_recovery_barge(
            barge_in=s.barge_in, interrupt_class=s.interrupt_class
        )
        for s in steps
    )
    has_silence = any(s.action == "wait" and s.silence_after_cue_ms > 0 for s in steps)
    if not has_barge and not has_silence:
        return None
    return ScriptVerifySpec(
        require_during_agent_speech=False,
        min_agent_finals_after_barge_in=1 if has_barge else 0,
        min_agent_finals_after_silence=0,
        min_agent_finals_after_first_cue=0,
    )


def apply_caller_behavior(
    persona: dict[str, Any],
    behavior_spec: dict[str, Any] | None,
    explicit_steps: list[ScriptStep],
    explicit_verify: ScriptVerifySpec | None,
    *,
    path_label: str = "scenario",
) -> tuple[list[ScriptStep], ScriptVerifySpec | None]:
    """Compile persona speech_conditions + Behavior and merge with explicit Script."""
    compiled: list[ScriptStep] = []
    compiled.extend(compile_from_speech_conditions(persona))
    if behavior_spec:
        compiled.extend(compile_from_behavior_spec(behavior_spec, f"{path_label}:Behavior"))
    steps = merge_script_steps(explicit_steps, compiled)
    verify = default_verify_for_compiled(steps, explicit_verify)
    return steps, verify
