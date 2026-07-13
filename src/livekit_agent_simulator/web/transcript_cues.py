"""Transcript finals → playback cue ranges for the report player."""

from __future__ import annotations

from typing import Any

from .report_time import _mono_to_audio_ms

# Align with observer: for *user* speech on the sim mic, Gemini is ground truth.
# Agent-side STT (lk.transcription / worker data) often mishears SIP audio as
# English (or duplicates) and has no matching PCM on conversation.wav L-channel.
_USER_SOURCE_RANK = {
    "sim.gemini": 0,
    "data": 2,
    "lk.transcription": 3,
}
_AGENT_SOURCE_RANK = {
    "data": 0,
    "lk.transcription": 1,
    "sim.gemini": 2,
}


def _source_rank(source: str | None, role: str) -> int:
    s = (source or "").strip()
    table = _USER_SOURCE_RANK if role == "user" else _AGENT_SOURCE_RANK
    if s in table:
        return table[s]
    # worker custom topics (e.g. voice_ai.transcript) ≈ data mid-priority
    if s and s not in ("sim.gemini", "lk.transcription"):
        return 1 if role == "user" else 0
    return 9


def _estimate_utterance_ms(text: str, *, role: str) -> int:
    """Heuristic speech duration from text length (final timestamps are end-of-utterance)."""
    t = (text or "").strip()
    if not t:
        return 800 if role == "agent" else 600
    words = [w for w in t.replace("\n", " ").split(" ") if w]
    units = max(len(words), max(1, len(t) // 4))
    ms = int(units * (95 if role == "agent" else 85))
    lo, hi = (700, 22_000) if role == "agent" else (500, 14_000)
    return max(lo, min(hi, ms))


def _collect_interim_starts(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[tuple[str, int, str, str]]:
    """(role, audio_ms, text, source) for interim transcripts."""
    out: list[tuple[str, int, str, str]] = []
    for e in events:
        kind = str(e.get("kind") or "")
        if not kind.startswith("transcript.") or not kind.endswith(".interim"):
            continue
        if "agent" in kind:
            role = "agent"
        elif "user" in kind:
            role = "user"
        else:
            continue
        text = ((e.get("spec") or {}).get("text") or "").strip()
        if not text:
            continue
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        ms = _mono_to_audio_ms(mono, t0, duration_ms)
        if ms is None:
            continue
        out.append((role, ms, text, str(e.get("source") or "")))
    return out


def _collect_agent_active_windows(
    events: list[dict[str, Any]],
    t0: int,
    duration_ms: int | None,
) -> list[tuple[int, int]]:
    """Agent speaking windows from room.active_speakers (audio_ms ranges)."""
    points: list[tuple[int, bool]] = []
    for e in events:
        if str(e.get("kind") or "") != "room.active_speakers":
            continue
        ids = (e.get("spec") or {}).get("identities") or []
        agent_on = any(
            str(i).startswith("agent-") or "agent" in str(i).lower() for i in ids
        )
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            continue
        ms = _mono_to_audio_ms(mono, t0, duration_ms)
        if ms is None:
            continue
        points.append((ms, agent_on))
    points.sort()

    windows: list[tuple[int, int]] = []
    start: int | None = None
    last_on: int | None = None
    gap_close_ms = 2800
    for ms, on in points:
        if on:
            if start is None:
                start = ms
            elif last_on is not None and ms - last_on > gap_close_ms:
                windows.append((start, last_on + 600))
                start = ms
            last_on = ms
        else:
            if start is not None and last_on is not None:
                windows.append((start, last_on + 600))
            start = None
            last_on = None
    if start is not None and last_on is not None:
        end = last_on + 600
        if duration_ms is not None:
            end = min(end, int(duration_ms))
        windows.append((start, end))

    if not windows:
        return windows
    windows.sort()
    merged: list[tuple[int, int]] = [windows[0]]
    for w0, w1 in windows[1:]:
        p0, p1 = merged[-1]
        if w0 <= p1 + 1500:
            merged[-1] = (p0, max(p1, w1))
        else:
            merged.append((w0, w1))
    return merged


def _best_interim_start(
    interims: list[tuple[str, int, str, str]],
    *,
    role: str,
    final_ms: int,
    text: str,
    est_ms: int,
    prefer_source: str | None = None,
) -> int | None:
    """Earliest interim growth signal (not a full-text dump at final time)."""
    window_lo = max(0, final_ms - est_ms - 3000)
    window_hi = final_ms - 500
    if window_hi <= window_lo:
        return None
    final_l = text.lower().strip()
    candidates: list[tuple[int, int]] = []  # (rank, ms)
    for r, ms, itext, src in interims:
        if r != role:
            continue
        if ms < window_lo or ms > window_hi:
            continue
        il = itext.lower().strip()
        if not il:
            continue
        if not (
            il in final_l
            or final_l.startswith(il[: min(12, len(il))])
            or il.startswith(final_l[: min(12, len(final_l))])
        ):
            continue
        rank = _source_rank(src, role)
        if prefer_source and src == prefer_source:
            rank = -1
        candidates.append((rank, ms))
    if not candidates:
        return None
    candidates.sort()
    # Among best rank, take earliest ms
    best_rank = candidates[0][0]
    return min(ms for rank, ms in candidates if rank == best_rank)


def _best_active_window(
    windows: list[tuple[int, int]],
    *,
    final_ms: int,
    est_ms: int,
) -> tuple[int, int] | None:
    best: tuple[int, int, int] | None = None
    for w0, w1 in windows:
        if final_ms < w0 - 300:
            continue
        if final_ms > w1 + 2000:
            continue
        span = max(1, w1 - w0)
        score = abs(w1 - final_ms) + abs(span - est_ms) // 3
        if best is None or score < best[0]:
            best = (score, w0, w1)
    if best is None:
        return None
    return best[1], best[2]


def _texts_similar(a: str, b: str) -> bool:
    a = a.lower().strip()
    b = b.lower().strip()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # token overlap for near-paraphrases from multi-STT
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter >= max(2, min(len(ta), len(tb)) * 0.5)


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
    # Critical for SIP: agent STT often invents English user lines that never hit sim mic.
    cues: list[dict[str, Any]] = []
    for c in raw:
        role = str(c["role"])
        replaced = False
        for i in range(len(cues) - 1, -1, -1):
            prev = cues[i]
            if prev["role"] != role:
                continue
            if abs(int(prev["final_ms"]) - int(c["final_ms"])) > 2500:
                # Too far from recent same-role cues — new utterance
                break
            if not _texts_similar(str(prev["text"]), str(c["text"])):
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
