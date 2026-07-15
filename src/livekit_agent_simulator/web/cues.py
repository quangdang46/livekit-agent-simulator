"""Build time-aligned transcript cues + behavior markers for the report player.

Public API (stable):
  - build_cues_payload(report_dir)
  - write_cues_json(report_dir)

Implementation is split across:
  report_time, transcript_cues, markers, speech_origin
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .markers import _build_markers
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
    MARKER_TOOL,
    MARKER_TOOL_ERROR,
    _load_events,
    _load_json,
    _resolve_audio_t0_ms,
    _wav_duration_ms,
)
from .tool_events import (
    _build_session_summary,
    _build_tool_spans,
    _build_tool_summary,
    _extract_chat_history,
)
from .speech_origin import _synthetic_script_barge_cues, _tag_cues_with_markers
from .transcript_cues import _build_transcript_cues

# Re-export marker constants for any external importers.
__all__ = [
    "MARKER_BARGE_IN",
    "MARKER_BACKCHANNEL",
    "MARKER_FALSE_INTERRUPT",
    "MARKER_DTMF",
    "MARKER_SCRIPT_CUE",
    "MARKER_SILENCE_WAIT",
    "MARKER_SILENCE",
    "MARKER_INTERRUPTION",
    "MARKER_RECOVERY",
    "MARKER_TOOL",
    "MARKER_TOOL_ERROR",
    "build_cues_payload",
    "write_cues_json",
]


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
    tool_events = _build_tool_spans(events, t0, duration_ms)
    markers = _build_markers(events, t0, duration_ms)
    _tag_cues_with_markers(cues, markers)
    # Guarantee inject-time cards even when STT misses or mis-attributes barge speech.
    cues.extend(_synthetic_script_barge_cues(markers, cues))

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
        from ..script import build_caller_behavior_summary

        behavior_summary = build_caller_behavior_summary(events)

    counts: dict[str, int] = {}
    for m in markers:
        t = str(m["type"])
        counts[t] = counts.get(t, 0) + 1

    config_snapshot = meta.get("config_snapshot") if isinstance(meta.get("config_snapshot"), dict) else {}
    observe_gaps = config_snapshot.get("observe_gaps")
    if not isinstance(observe_gaps, list):
        observe = config_snapshot.get("observe") if isinstance(config_snapshot.get("observe"), dict) else {}
        inner = observe.get("observe_gaps")
        observe_gaps = inner if isinstance(inner, list) else []

    summary_dict = summary if isinstance(summary, dict) else {}
    tool_summary = _build_tool_summary(tool_events, summary_dict)

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
        "tool_events": tool_events,
        "tool_summary": tool_summary,
        "session_summary": _build_session_summary(events, t0, duration_ms),
        "chat_history": _extract_chat_history(events),
        "observe_gaps": observe_gaps,
    }


def write_cues_json(report_dir: Path) -> Path:
    """Write ``cues.json`` into the report dir; return path."""
    report_dir = Path(report_dir)
    payload = build_cues_payload(report_dir)
    out = report_dir / "cues.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
