"""Behavior markers (barge / silence / recovery) aligned to conversation audio."""

from __future__ import annotations

from typing import Any

from .report_time import (
    MARKER_BACKCHANNEL,
    MARKER_BARGE_IN,
    MARKER_DTMF,
    MARKER_FALSE_INTERRUPT,
    MARKER_INTERRUPTION,
    MARKER_RECOVERY,
    MARKER_SCRIPT_CUE,
    MARKER_SILENCE,
    MARKER_SILENCE_WAIT,
    _clamp_end,
    _mono_to_audio_ms,
)
from .speech_origin import _text_overlap
from .tool_events import _build_tool_spans, _tool_spans_to_markers

def _collect_script_injects(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    """room_pcm / barge injects with real play duration (audio is heard immediately)."""
    out: list[dict[str, Any]] = []
    for e in events:
        if str(e.get("kind") or "") != "sim.script_inject":
            continue
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        start = _mono_to_audio_ms(mono, t0, duration_ms)
        if start is None:
            continue
        spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}
        try:
            dur = int(spec.get("duration_ms") or 0)
        except (TypeError, ValueError):
            dur = 0
        # gemini_text barge has no duration_ms — allow ~2s of audible speech
        if dur <= 0:
            delivery = str(spec.get("delivery") or "")
            dur = 2200 if delivery != "room_pcm" else 800
        out.append(
            {
                "start_ms": start,
                "duration_ms": dur,
                "end_ms": start + max(200, dur),
                "label": str(spec.get("label") or ""),
                "text": str(spec.get("text") or ""),
                "delivery": str(spec.get("delivery") or ""),
                "asset": spec.get("asset"),
            }
        )
    return out


def _inject_duration_near(
    injects: list[dict[str, Any]],
    at_ms: int,
    *,
    label: str = "",
    say: str = "",
) -> int | None:
    best: int | None = None
    best_d = 10_000
    for inj in injects:
        d = abs(int(inj["start_ms"]) - at_ms)
        if d > 900:
            continue
        # Prefer same label / text when available
        same = False
        if label and inj.get("label") and (
            label in str(inj["label"]) or str(inj["label"]) in label
        ):
            same = True
        if say and inj.get("text") and _text_overlap(say, str(inj["text"])):
            same = True
        score_d = d - (200 if same else 0)
        if score_d < best_d:
            best_d = score_d
            best = int(inj["duration_ms"])
    return best


def _build_markers(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    """Extract barge-in / silence / interruption / recovery markers aligned to audio."""
    markers: list[dict[str, Any]] = []
    barge_points: list[int] = []  # audio start_ms of barge-ins (for recovery)
    injects = _collect_script_injects(events, t0, duration_ms)

    for e in events:
        kind = str(e.get("kind") or "")
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        start = _mono_to_audio_ms(mono, t0, duration_ms)
        if start is None:
            continue
        spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}

        if kind == "sim.script.cue":
            barge = bool(spec.get("barge_in"))
            step_id = str(spec.get("step_id") or "")
            label = str(spec.get("label") or step_id or "script cue")
            say = str(spec.get("say") or "").strip()
            during = bool(spec.get("during_agent_speech"))
            waited = int(spec.get("waited_ms") or 0)
            icls = str(spec.get("class") or spec.get("interrupt_class") or "").strip() or None
            mtype = MARKER_BARGE_IN if barge else MARKER_SCRIPT_CUE
            # Non-recovery classes get distinct marker types for UI chips
            if icls == "backchannel":
                mtype = MARKER_BACKCHANNEL
            elif icls == "noise":
                mtype = MARKER_FALSE_INTERRUPT
            elif icls == "dtmf":
                mtype = MARKER_DTMF
            detail_parts = [
                f"trigger={spec.get('trigger') or '?'}",
                f"during_agent={during}",
            ]
            if icls:
                detail_parts.append(f"class={icls}")
            if say:
                detail_parts.append(f'say="{say}"')
            if waited:
                detail_parts.append(f"waited={waited}ms")
            # Prefer real inject play length so scrubber/highlight match audible audio.
            inj_dur = _inject_duration_near(injects, start, label=label, say=say)
            if barge and icls not in ("noise", "backchannel"):
                span = max(2200 if during else 1400, (inj_dur or 0) + 400)
            else:
                span = max(400, min(waited, 2000) or 400, (inj_dur or 0) + 200)
            end = _clamp_end(start, start + span, duration_ms)
            prefix = ""
            if barge and during and icls not in ("noise", "backchannel"):
                prefix = "⚡ "
            elif icls == "backchannel":
                prefix = "💬 "
            elif icls == "noise":
                prefix = "🔇 "
            markers.append(
                {
                    "type": mtype,
                    "start_ms": start,
                    "end_ms": end,
                    "label": prefix + label,
                    "detail": " · ".join(detail_parts),
                    "step_id": step_id or None,
                    "say": say or None,
                    "during_agent_speech": during,
                    "barge_in": barge,
                    "class": icls,
                    "audio_ms": inj_dur or span,
                }
            )
            # Only recovery barges feed recovery markers
            if barge and icls not in ("noise", "backchannel", "dtmf", "silence"):
                barge_points.append(start)
            continue

        if kind == "sim.script.wait":
            step_id = str(spec.get("step_id") or "")
            label = str(spec.get("label") or step_id or "user pause")
            waited = int(spec.get("waited_ms") or 0)
            # Wait condition held for waited_ms ending at fire time.
            span = waited if waited > 0 else 1500
            win_start = max(0, start - span)
            end = _clamp_end(win_start, start + 200, duration_ms)
            markers.append(
                {
                    "type": MARKER_SILENCE_WAIT,
                    "start_ms": win_start,
                    "end_ms": end,
                    "label": label,
                    "detail": (
                        f"script wait · trigger={spec.get('trigger') or 'silence'} · "
                        f"held≈{span}ms"
                    ),
                    "step_id": step_id or None,
                    "trigger": spec.get("trigger"),
                }
            )
            continue

        if kind == "silence.detected":
            duration = int(spec.get("duration_ms") or 0)
            span = duration if duration > 0 else 4000
            win_start = max(0, start - span)
            end = _clamp_end(win_start, start, duration_ms)
            markers.append(
                {
                    "type": MARKER_SILENCE,
                    "start_ms": win_start,
                    "end_ms": end,
                    "label": "silence detected",
                    "detail": f"observer silence ≥ threshold ({span}ms)",
                    "duration_ms": span,
                }
            )
            continue

        if kind == "interruption":
            by = str(spec.get("by") or "unknown")
            note = str(spec.get("note") or "").strip()
            end = _clamp_end(start, start + 500, duration_ms)
            markers.append(
                {
                    "type": MARKER_INTERRUPTION,
                    "start_ms": start,
                    "end_ms": end,
                    "class": (str(spec.get("class") or "").strip() or None),
                    "label": f"interruption ({by})",
                    "detail": note or f"by={by}",
                    "by": by,
                }
            )
            continue

    # Recovery: first agent final after each barge-in (agent spoke again).
    agent_finals: list[int] = []
    for e in events:
        kind = str(e.get("kind") or "")
        if kind != "transcript.agent.final":
            continue
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        start = _mono_to_audio_ms(mono, t0, duration_ms)
        if start is None:
            continue
        agent_finals.append(start)

    used_agent: set[int] = set()
    for barge_ms in barge_points:
        recovery_ms = next((a for a in agent_finals if a > barge_ms and a not in used_agent), None)
        if recovery_ms is None:
            continue
        used_agent.add(recovery_ms)
        end = _clamp_end(recovery_ms, recovery_ms + 800, duration_ms)
        markers.append(
            {
                "type": MARKER_RECOVERY,
                "start_ms": recovery_ms,
                "end_ms": end,
                "label": "agent recovery",
                "detail": f"agent final after barge-in @ {barge_ms}ms",
                "after_barge_ms": barge_ms,
            }
        )

    tool_spans = _build_tool_spans(events, t0, duration_ms)
    markers.extend(_tool_spans_to_markers(tool_spans))

    markers.sort(key=lambda m: (m["start_ms"], m["type"]))
    return markers


