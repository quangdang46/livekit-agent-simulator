"""Classify user-channel speech as natural caller vs script inject."""

from __future__ import annotations

from typing import Any

from .report_time import MARKER_BARGE_IN, MARKER_SCRIPT_CUE

def _norm_speech(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in s).split())


# Common particles / function words that must not alone prove script↔STT match.
_CONTENT_STOP = frozenset(
    {
        "được",
        "không",
        "mình",
        "bạn",
        "với",
        "cho",
        "của",
        "này",
        "đó",
        "là",
        "và",
        "các",
        "một",
        "như",
        "để",
        "có",
        "thì",
        "rồi",
        "nữa",
        "when",
        "what",
        "that",
        "this",
        "with",
        "have",
        "from",
        "your",
        "will",
        "been",
        "were",
        "they",
        "them",
        "than",
        "then",
        "also",
        "just",
        "into",
        "over",
        "more",
        "hook",  # alone too weak without "khoan"
    }
)


def _text_overlap(a: str, b: str) -> bool:
    """Match script say ↔ STT without false positives on common particles.

    Prefer phrase substring; else require distinctive content words (not
    particles like được/không that appear in almost every VI turn).
    """
    na, nb = _norm_speech(a), _norm_speech(b)
    if not na or not nb:
        return False
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 5 and shorter in longer:
        return True
    # Prefer matching against the shorter side as the "script say" fingerprint.
    wa = {w for w in na.split() if len(w) >= 4 and w not in _CONTENT_STOP}
    wb = {w for w in nb.split() if len(w) >= 4 and w not in _CONTENT_STOP}
    inter = wa & wb
    if not inter:
        return False
    if any(len(w) >= 5 for w in inter):
        return True
    return len(inter) >= 2


def _mostly_script_say(text: str, say: str) -> bool:
    """True when STT is essentially the script line (not freestyle + inject concat).

    Bootstrap freestyle often precedes Script open; STT merges both into one final.
    Those must stay ``natural``, not ``script_cue``.
    """
    nt, ns = _norm_speech(text), _norm_speech(say)
    if not ns or not nt:
        return False
    if nt == ns:
        return True
    if ns in nt:
        extras = (" " + nt + " ").replace(" " + ns + " ", " ", 1).strip()
        if not extras:
            return True
        if len(extras) <= 12 and len(extras.split()) <= 2:
            return True
        content = [w for w in extras.split() if len(w) >= 4 and w not in _CONTENT_STOP]
        # Substantial leftover content → freestyle mixed with cue.
        if len(content) >= 2 or len(extras) > max(24, int(len(ns) * 0.45)):
            return False
        return True
    if nt in ns and len(ns) - len(nt) <= 20:
        return True
    # Word-overlap only if lengths are similar (avoid tagging long freestyle).
    if not _text_overlap(text, say):
        return False
    return len(nt) <= len(ns) + 24


def _pin_script_window(
    c: dict[str, Any],
    matched: dict[str, Any],
    *,
    origin: str,
    final_ms: int,
) -> None:
    if matched.get("step_id"):
        c["script_step_id"] = matched.get("step_id")
    if matched.get("say"):
        c["script_say"] = matched.get("say")
    if matched.get("label"):
        c["script_label"] = matched.get("label")
    inject_ms = int(matched.get("start_ms") or final_ms)
    try:
        audio_ms = int(matched.get("audio_ms") or 0)
    except (TypeError, ValueError):
        audio_ms = 0
    if audio_ms <= 0:
        audio_ms = 2200 if origin == "script_barge" else 900
    c["start_ms"] = max(0, inject_ms - 80)
    c["end_ms"] = max(
        final_ms + 500,
        inject_ms + audio_ms + 350,
        int(c.get("end_ms") or 0),
    )
    c["inject_ms"] = inject_ms


def _tag_cues_with_markers(
    cues: list[dict[str, Any]], markers: list[dict[str, Any]]
) -> None:
    """Attach nearby marker types + classify script barge speech vs natural caller."""
    barge_markers = [
        m
        for m in markers
        if m.get("type") == MARKER_BARGE_IN or m.get("barge_in")
    ]
    script_markers = [
        m for m in markers if m.get("type") in (MARKER_BARGE_IN, MARKER_SCRIPT_CUE)
    ]

    for c in cues:
        tags: list[str] = []
        start = int(c["start_ms"])
        end = int(c.get("end_ms") or start)
        final_ms = int(c.get("final_ms") if c.get("final_ms") is not None else end)
        for m in markers:
            mtype = str(m["type"])
            ms = int(m["start_ms"])
            me = int(m.get("end_ms") or ms)
            near = (
                abs(ms - final_ms) <= 8000
                or abs(ms - start) <= 1200
                or (ms <= end and me >= start)
            )
            if not near:
                continue
            if mtype not in tags:
                tags.append(mtype)
        if tags:
            c["marker_tags"] = tags

        # User channel audio that is really a Script barge/inject — not persona chat.
        if str(c.get("role")) != "user":
            c["speech_origin"] = "natural"
            continue

        text = str(c.get("text") or "")
        origin = "natural"
        matched: dict[str, Any] | None = None
        best_score = -1

        for m in script_markers:
            ms = int(m["start_ms"])
            say = str(m.get("say") or "")
            is_barge = bool(m.get("barge_in") or m.get("type") == MARKER_BARGE_IN)
            # STT often lags inject by several seconds (especially LiveKit STT).
            delta = final_ms - ms  # >0 ⇒ transcript after inject
            if delta < -800 or delta > 15000:
                continue
            text_hit = (
                _mostly_script_say(text, say)
                if say and not str(say).startswith("[")
                else False
            )
            # Without text match: only ultra-short STT near inject ("khoan đã", "uh-huh").
            word_n = len(text.split())
            tiny = word_n <= 3 and len(text.strip()) <= 28
            if is_barge:
                if text_hit and -500 <= delta <= 15000:
                    accept = True
                    score = 100 - min(40, max(0, delta) // 400)
                elif tiny and 0 <= delta <= 3500:
                    accept = True
                    score = 70 - min(30, delta // 200)
                else:
                    accept = False
                    score = 0
            else:
                accept = text_hit and 0 <= delta <= 8000
                score = 50 if accept else 0
            if accept and score > best_score:
                best_score = score
                matched = m
                origin = "script_barge" if is_barge else "script_cue"

        # Time-only fallback: 1–2 word STT near inject.
        if origin == "natural" and len(text.split()) <= 2 and len(text.strip()) <= 24:
            for m in barge_markers:
                ms = int(m["start_ms"])
                delta = final_ms - ms
                if 0 <= delta <= 3500:
                    matched = m
                    origin = "script_barge"
                    break

        # Late STT of barge text (e.g. "khoan đã" many seconds after inject):
        # score by phrase quality first, then time closeness.
        if origin == "natural" and len(text.split()) <= 4:
            best_m = None
            best_key: tuple[int, int] | None = None
            nt = _norm_speech(text)
            for m in barge_markers:
                say = str(m.get("say") or "")
                if not say or str(say).startswith("["):
                    continue
                if not _text_overlap(text, say):
                    continue
                ms = int(m["start_ms"])
                delta = final_ms - ms
                if delta < -500:
                    continue
                ns = _norm_speech(say)
                # Prefer full phrase containment (khoan đã ⊂ cut-in-1 say)
                phrase = 2 if nt and ns and (nt in ns or ns in nt) else 1
                # Higher phrase, then closer in time
                key = (phrase, -abs(delta))
                if best_key is None or key > best_key:
                    best_key = key
                    best_m = m
            if best_m is not None:
                matched = best_m
                origin = "script_barge"

        c["speech_origin"] = origin
        if matched is not None:
            _pin_script_window(c, matched, origin=origin, final_ms=final_ms)
            # Prefer full script line on the card; keep STT fragment for detail.
            say = str(matched.get("say") or "").strip()
            if (
                say
                and not say.startswith("[")
                and len(text.strip()) < len(say)
                and len(text.split()) <= 6
            ):
                c["stt_text"] = text
                c["text"] = say


def _synthetic_script_barge_cues(
    markers: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Always show a center-column card at inject time — do not rely on laggy STT.

    Real runs often never get a clean user.final for gemini_text barge, or STT
    arrives many seconds later and looks like natural Caller speech.
    """
    covered_steps: set[str] = set()
    covered_injects: list[int] = []
    for c in existing:
        if c.get("speech_origin") not in ("script_barge", "script_cue"):
            continue
        sid = c.get("script_step_id")
        if sid:
            covered_steps.add(str(sid))
        if c.get("inject_ms") is not None:
            try:
                covered_injects.append(int(c["inject_ms"]))
            except (TypeError, ValueError):
                pass

    out: list[dict[str, Any]] = []
    for m in markers:
        if not (m.get("barge_in") or m.get("type") == MARKER_BARGE_IN):
            continue
        step_id = str(m.get("step_id") or "")
        inject_ms = int(m.get("start_ms") or 0)
        if step_id and step_id in covered_steps:
            continue
        if any(abs(inject_ms - t) < 600 for t in covered_injects):
            continue
        say = str(m.get("say") or "").strip()
        label = str(m.get("label") or step_id or "script barge").strip()
        # Prefer human script text; bracket placeholders → use label
        display = say if say and not (say.startswith("[") and say.endswith("]")) else label
        if display.startswith("⚡"):
            display = display.lstrip("⚡ ").strip()
        try:
            audio_ms = int(m.get("audio_ms") or 0)
        except (TypeError, ValueError):
            audio_ms = 0
        if audio_ms <= 0:
            audio_ms = max(1200, int(m.get("end_ms") or inject_ms) - inject_ms)
        end_ms = max(inject_ms + audio_ms + 350, inject_ms + 1200)
        out.append(
            {
                "role": "user",
                "start_ms": max(0, inject_ms - 80),
                "end_ms": end_ms,
                "final_ms": end_ms,
                "text": display,
                "speech_origin": "script_barge",
                "script_step_id": step_id or None,
                "script_say": say or display,
                "script_label": label,
                "inject_ms": inject_ms,
                "synthetic": True,
                "source": "sim.script",
                "marker_tags": [MARKER_BARGE_IN],
            }
        )
        if step_id:
            covered_steps.add(step_id)
        covered_injects.append(inject_ms)
    return out


