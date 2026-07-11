"""Build time-aligned transcript cues for the report player."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any


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


def build_cues_payload(report_dir: Path) -> dict[str, Any]:
    """Return cues.json body for a single run report directory."""
    report_dir = Path(report_dir)
    run_id = report_dir.name
    events = _load_events(report_dir / "events.jsonl")
    meta: dict[str, Any] = {}
    meta_path = report_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}

    wav_path = report_dir / "conversation.wav"
    duration_ms = _wav_duration_ms(wav_path)
    audio_meta = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
    if duration_ms is None and audio_meta.get("duration_ms") is not None:
        try:
            duration_ms = int(audio_meta["duration_ms"])
        except (TypeError, ValueError):
            duration_ms = None

    t0 = _resolve_audio_t0_ms(meta, events)

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
        start_ms = max(0, mono - t0)
        if duration_ms is not None and start_ms > duration_ms + 2000:
            # Far past audio end — skip (dispatch/setup chatter on mono clock)
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

    return {
        "run_id": run_id,
        "scenario_id": meta.get("scenario_id"),
        "audio": {
            "file": "conversation.wav" if wav_path.exists() else None,
            "duration_ms": duration_ms,
            "t0_mono_ms": t0,
            "channels": audio_meta.get("channels") or {"left": "sim", "right": "agent"},
        },
        "cues": cues,
    }


def write_cues_json(report_dir: Path) -> Path:
    """Write ``cues.json`` into the report dir; return path."""
    report_dir = Path(report_dir)
    payload = build_cues_payload(report_dir)
    out = report_dir / "cues.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
