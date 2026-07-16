"""Hang-up control marker for the simulated Gemini caller.

Native-audio Live sessions only emit spoken audio + ASR transcription — there is
no silent text channel for ``[END_CALL]``. Models often vocalize English
\"end call\", which leaks into the LiveKit room. Helpers detect / strip both the
bracket token and spoken hang-up / farewell phrases so the bridge can mute leftover
PCM, and (when a Script is still armed) defer freestyle hang-up.
"""

from __future__ import annotations

import re

END_CALL_TOKEN = "[END_CALL]"

# Spoken English forms Gemini often utters when asked to emit the harness token.
_SPOKEN_END_RE = re.compile(
    r"(?i)(?:\[\s*end[_\s\-]*call\s*\]|\bend[_\s\-]*call\b|\bhang[_\s\-]*up\b)[.!?]*"
)

# Soft farewells that make the agent under test call end_call, even without [END_CALL].
# Used to mute / defer while timed Script steps remain (portable; not locale-specific words only).
_FAREWELL_RE = re.compile(
    r"(?i)(?:"
    r"\bgood\s*bye\b|\bgoodbye\b|\bbye[\s\-]?bye\b|\bbye\b|"
    r"\bsee\s+you(?:\s+later)?\b|\btalk\s+later\b|\bthat'?s\s+all\b|"
    r"tạm\s*biệt|kết\s*thúc|cúp\s*máy"
    r")[.!?]*"
)


def contains_end_call_signal(text: str) -> bool:
    if not text:
        return False
    if END_CALL_TOKEN in text:
        return True
    return _SPOKEN_END_RE.search(text) is not None


def contains_farewell_signal(text: str) -> bool:
    """True for bye/goodbye-style closings (with or without harness token)."""
    if not text:
        return False
    if contains_end_call_signal(text):
        return True
    return _FAREWELL_RE.search(text) is not None


def strip_end_call_signal(text: str) -> str:
    if not text:
        return ""
    out = text.replace(END_CALL_TOKEN, " ")
    out = _SPOKEN_END_RE.sub(" ", out)
    # Collapse whitespace and stray punctuation left by the substitute.
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r"[,\s]+$", "", out)
    return " ".join(out.split()).strip()


def strip_farewell_signal(text: str) -> str:
    """Strip harness hang-up markers and soft farewell words for transcript logging."""
    if not text:
        return ""
    out = strip_end_call_signal(text)
    out = _FAREWELL_RE.sub(" ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r"[,\s]+$", "", out)
    return " ".join(out.split()).strip()


def should_end_call_on_turn(
    *,
    pending_script: bool,
    ended: bool,
    farewell: bool,
    scripted_farewell: bool,
) -> bool:
    """True when dialogue freestyle should tear down the room.

    Soft bye alone is enough when no Script owns hang-up — Gemini often says
    \"Bye\" without emitting ``[END_CALL]``. Script-pending turns defer instead.
    """
    if scripted_farewell:
        return False
    if pending_script:
        return False
    return bool(ended or farewell)
