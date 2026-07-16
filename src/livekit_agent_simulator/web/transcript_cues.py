"""Transcript finals → playback cue ranges for the report player."""

from __future__ import annotations

from typing import Any

from .cue_helpers.source_priority import texts_similar as _texts_similar
from .cue_helpers.source_priority import source_rank as _source_rank
from .cue_helpers.windows import (
    estimate_utterance_ms as _estimate_utterance_ms,
    collect_interim_starts as _collect_interim_starts,
    collect_agent_active_windows as _collect_agent_active_windows,
    best_interim_start as _best_interim_start,
    best_active_window as _best_active_window,
)
from .report_time import _mono_to_audio_ms


def _build_transcript_cues(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    interims = _collect_interim_starts(events, t0, duration_ms)
    agent_windows = _collect_agent_active_windows(events, t0, duration_ms)

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
        final_ms = _mono_to_audio_ms(mono, t0, duration_ms)
        if final_ms is None:
            continue
        raw.append(
            {
                "role": role,
                "final_ms": final_ms,
                "text": text,
                "turn": e.get("turn"),
                "source": e.get("source"),
                "kind": kind,
                "ts_mono_ms": mono,
            }
        )

    # Sort by time; for same role+time prefer higher-quality source
    raw.sort(
        key=lambda c: (
            c["final_ms"],
            _source_rank(c.get("source"), str(c["role"])),
            0 if c["role"] == "agent" else 1,
        )
    )

    # Collapse multi-source duplicates of the *same utterance*.
    # Agent-side STT of the caller often finalizes several seconds after Gemini's
    # output transcription (turn boundary), so the near-window must be wide when
    # texts match across sources — otherwise the player shows duplicate callers.
    cues: list[dict[str, Any]] = []
    for c in raw:
        role = str(c["role"])
        replaced = False
        for i in range(len(cues) - 1, -1, -1):
            prev = cues[i]
            if prev["role"] != role:
                continue
            delta = abs(int(prev["final_ms"]) - int(c["final_ms"]))
            similar = _texts_similar(str(prev["text"]), str(c["text"]))
            prev_src = str(prev.get("source") or "")
            cur_src = str(c.get("source") or "")
            cross_source = bool(prev_src and cur_src and prev_src != cur_src)
            # Same-source near dupes stay tight; cross-source STT lag needs headroom.
            if similar and cross_source:
                max_delta = 15000 if role == "user" else 6000
            elif similar:
                max_delta = 4000
            else:
                max_delta = 2500
            if delta > max_delta:
                # Farther same-role cues cannot be this utterance.
                break
            if not similar:
                continue
            prev_rank = _source_rank(prev.get("source"), role)
            cur_rank = _source_rank(c.get("source"), role)
            if cur_rank < prev_rank or (
                cur_rank == prev_rank and len(str(c["text"])) > len(str(prev["text"]))
            ):
                cues[i] = c
            # Drop inferior / equal duplicate (already represented)
            replaced = True
            break
        if not replaced:
            cues.append(c)

    # Drop agent-side STT *ghosts* of the same Gemini utterance only.
    # Ghost = non-gemini user final within ±2.5s of a sim.gemini final with
    # *dissimilar* text (English hallucination). Similar text already collapsed.
    # Do NOT drop unrelated user lines (script barge STT, later natural turns).
    gemini_user = [
        c
        for c in cues
        if c["role"] == "user" and str(c.get("source") or "") == "sim.gemini"
    ]
    if gemini_user:
        filtered: list[dict[str, Any]] = []
        for c in cues:
            if c["role"] != "user":
                filtered.append(c)
                continue
            src = str(c.get("source") or "")
            if src == "sim.gemini":
                filtered.append(c)
                continue
            fm = int(c["final_ms"])
            text = str(c.get("text") or "")
            # Only consider as ghost if very close in time AND clearly not same text
            # AND source is agent-side STT (lk.transcription / worker topic)
            if src in ("lk.transcription",) or (
                src and src not in ("sim.gemini", "sim.script") and "transcript" in src
            ):
                near = [
                    g
                    for g in gemini_user
                    if abs(fm - int(g["final_ms"])) <= 2500
                ]
                if near and not any(
                    _texts_similar(text, str(g.get("text") or "")) for g in near
                ):
                    # e.g. Gemini "Alô Lan" vs STT "How's your day going?"
                    continue
            filtered.append(c)
        cues = filtered

    for c in cues:
        final_ms = int(c["final_ms"])
        role = str(c["role"])
        text = str(c.get("text") or "")
        est = _estimate_utterance_ms(text, role=role)

        start: int | None = None
        end_hint: int | None = None

        if role == "agent":
            win = _best_active_window(agent_windows, final_ms=final_ms, est_ms=est)
            if win is not None:
                start, end_hint = win

        prefer = "sim.gemini" if role == "user" else None
        interim_start = _best_interim_start(
            interims,
            role=role,
            final_ms=final_ms,
            text=text,
            est_ms=est,
            prefer_source=prefer,
        )
        if interim_start is not None:
            if start is None or interim_start < start:
                start = interim_start

        if start is None:
            start = max(0, final_ms - est)

        if start >= final_ms:
            start = max(0, final_ms - 400)
        start = max(0, min(start, final_ms - 200))

        tail = 350
        end = final_ms + tail
        if end_hint is not None and end_hint > final_ms:
            end = max(end, min(end_hint, final_ms + 800))
        if duration_ms is not None:
            end = min(end, max(start + 200, int(duration_ms)))
        end = max(start + 200, end)

        c["start_ms"] = int(start)
        c["end_ms"] = int(end)

    cues.sort(key=lambda c: (c["start_ms"], 0 if c["role"] == "agent" else 1))
    return cues
