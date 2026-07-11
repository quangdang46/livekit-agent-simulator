"""Build time-aligned transcript cues + behavior markers for the report player."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any


# Marker kinds exposed to the report player (stable API for the UI).
MARKER_BARGE_IN = "barge_in"
MARKER_SCRIPT_CUE = "script_cue"
MARKER_SILENCE_WAIT = "silence_wait"
MARKER_SILENCE = "silence"
MARKER_INTERRUPTION = "interruption"
MARKER_RECOVERY = "recovery"


def _wav_duration_ms(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return int(frames * 1000 / rate)
    except Exception:
        return None


def _load_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not events_path.exists():
        return events
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _resolve_audio_t0_ms(meta: dict[str, Any], events: list[dict[str, Any]]) -> int:
    audio = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
    if audio.get("t0_mono_ms") is not None:
        try:
            return max(0, int(audio["t0_mono_ms"]))
        except (TypeError, ValueError):
            pass
    # Fallback for older reports: first transcript-ish event.
    for e in events:
        kind = str(e.get("kind") or "")
        if kind.startswith("transcript.") or kind in (
            "sim.mic_published",
            "sim.gemini_connected",
        ):
            try:
                return max(0, int(e.get("ts_mono_ms") or 0))
            except (TypeError, ValueError):
                continue
    return 0


def _mono_to_audio_ms(mono: int, t0: int, duration_ms: int | None) -> int | None:
    start_ms = max(0, mono - t0)
    if duration_ms is not None and start_ms > duration_ms + 2000:
        return None
    return start_ms


def _clamp_end(start_ms: int, end_ms: int, duration_ms: int | None) -> int:
    end = max(start_ms + 120, end_ms)
    if duration_ms is not None:
        end = min(end, max(start_ms + 120, duration_ms))
    return end


def _build_markers(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    """Extract barge-in / silence / interruption / recovery markers aligned to audio."""
    markers: list[dict[str, Any]] = []
    barge_points: list[int] = []  # audio start_ms of barge-ins (for recovery)

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
            mtype = MARKER_BARGE_IN if barge else MARKER_SCRIPT_CUE
            detail_parts = [
                f"trigger={spec.get('trigger') or '?'}",
                f"during_agent={during}",
            ]
            if say:
                detail_parts.append(f'say="{say}"')
            if waited:
                detail_parts.append(f"waited={waited}ms")
            # Barge-in: longer visible band so cut-in is easy to spot on the scrubber.
            if barge:
                span = 1200 if during else 700
            else:
                span = max(400, min(waited, 2000) or 400)
            end = _clamp_end(start, start + span, duration_ms)
            markers.append(
                {
                    "type": mtype,
                    "start_ms": start,
                    "end_ms": end,
                    "label": ("⚡ " if barge and during else "") + label,
                    "detail": " · ".join(detail_parts),
                    "step_id": step_id or None,
                    "say": say or None,
                    "during_agent_speech": during,
                    "barge_in": barge,
                }
            )
            if barge:
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

    markers.sort(key=lambda m: (m["start_ms"], m["type"]))
    return markers


def _build_transcript_cues(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for e in events:
        kind = str(e.get("kind") or "")
        if not kind.startswith("transcript.") or not kind.endswith(".final"):
            continue
        spec = e.get("spec") or {}
        text = (spec.get("text") or "").strip()
        if not text:
            continue
        if "agent" in kind:
            role = "agent"
        elif "user" in kind:
            role = "user"
        else:
            continue
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        start_ms = _mono_to_audio_ms(mono, t0, duration_ms)
        if start_ms is None:
            continue
        raw.append(
            {
                "role": role,
                "start_ms": start_ms,
                "text": text,
                "turn": e.get("turn"),
                "source": e.get("source"),
                "kind": kind,
                "ts_mono_ms": mono,
            }
        )

    # Prefer higher-quality sources if duplicates at same role+approx time
    raw.sort(key=lambda c: (c["start_ms"], 0 if c["role"] == "agent" else 1))
    cues: list[dict[str, Any]] = []
    for c in raw:
        if cues:
            prev = cues[-1]
            if (
                prev["role"] == c["role"]
                and abs(prev["start_ms"] - c["start_ms"]) < 800
                and (prev["text"] in c["text"] or c["text"] in prev["text"])
            ):
                # Keep longer text
                if len(c["text"]) > len(prev["text"]):
                    cues[-1] = c
                continue
        cues.append(c)

    for i, c in enumerate(cues):
        if i + 1 < len(cues):
            end = cues[i + 1]["start_ms"]
        elif duration_ms is not None:
            end = duration_ms
        else:
            end = c["start_ms"] + 3000
        c["end_ms"] = max(c["start_ms"] + 200, end)

    return cues


def _tag_cues_with_markers(
    cues: list[dict[str, Any]], markers: list[dict[str, Any]]
) -> None:
    """Attach nearby marker types onto transcript cues for UI badges."""
    for c in cues:
        tags: list[str] = []
        start = int(c["start_ms"])
        end = int(c.get("end_ms") or start)
        for m in markers:
            mtype = str(m["type"])
            ms = int(m["start_ms"])
            me = int(m.get("end_ms") or ms)
            # Overlap or within 1.2s of cue start
            near = abs(ms - start) <= 1200 or (ms <= end and me >= start)
            if not near:
                continue
            if mtype not in tags:
                tags.append(mtype)
        if tags:
            c["marker_tags"] = tags


def build_cues_payload(report_dir: Path) -> dict[str, Any]:
    """Return cues.json body for a single run report directory."""
    report_dir = Path(report_dir)
    run_id = report_dir.name
    events = _load_events(report_dir / "events.jsonl")
    meta = _load_json(report_dir / "meta.json")
    summary = _load_json(report_dir / "summary.json")

    wav_path = report_dir / "conversation.wav"
    duration_ms = _wav_duration_ms(wav_path)
    audio_meta = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
    if duration_ms is None and audio_meta.get("duration_ms") is not None:
        try:
            duration_ms = int(audio_meta["duration_ms"])
        except (TypeError, ValueError):
            duration_ms = None

    t0 = _resolve_audio_t0_ms(meta, events)
    cues = _build_transcript_cues(events, t0, duration_ms)
    markers = _build_markers(events, t0, duration_ms)
    _tag_cues_with_markers(cues, markers)

    script_verify = summary.get("script_verify") if isinstance(summary, dict) else None
    assert_verify = summary.get("assert_verify") if isinstance(summary, dict) else None
    if not isinstance(script_verify, dict):
        # Fallback: last script.verify event
        for e in reversed(events):
            if e.get("kind") == "script.verify" and isinstance(e.get("spec"), dict):
                script_verify = e["spec"]
                break
    if not isinstance(assert_verify, dict):
        for e in reversed(events):
            if e.get("kind") == "assert.verify" and isinstance(e.get("spec"), dict):
                assert_verify = e["spec"]
                break

    caller = summary.get("caller") if isinstance(summary, dict) else None
    behavior_summary = None
    if isinstance(caller, dict) and isinstance(caller.get("behavior_summary"), dict):
        behavior_summary = caller["behavior_summary"]
    elif isinstance(summary, dict) and isinstance(summary.get("behavior_summary"), dict):
        behavior_summary = summary["behavior_summary"]
    if behavior_summary is None and events:
        # Older reports / live API without summary field — recompute from events.
        from ..script_runner import build_caller_behavior_summary

        behavior_summary = build_caller_behavior_summary(events)

    counts: dict[str, int] = {}
    for m in markers:
        t = str(m["type"])
        counts[t] = counts.get(t, 0) + 1

    return {
        "run_id": run_id,
        "scenario_id": meta.get("scenario_id") or summary.get("scenario_id"),
        "audio": {
            "file": "conversation.wav" if wav_path.exists() else None,
            "duration_ms": duration_ms,
            "t0_mono_ms": t0,
            "channels": audio_meta.get("channels") or {"left": "sim", "right": "agent"},
        },
        "cues": cues,
        "markers": markers,
        "marker_counts": counts,
        "script_verify": script_verify,
        "assert_verify": assert_verify,
        "caller": {"behavior_summary": behavior_summary} if behavior_summary is not None else None,
        "behavior_summary": behavior_summary,
    }


def write_cues_json(report_dir: Path) -> Path:
    """Write ``cues.json`` into the report dir; return path."""
    report_dir = Path(report_dir)
    payload = build_cues_payload(report_dir)
    out = report_dir / "cues.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
