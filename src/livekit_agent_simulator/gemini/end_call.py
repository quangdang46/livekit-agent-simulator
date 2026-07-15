"""Hang-up control marker for the simulated Gemini caller.

Native-audio Live sessions only emit spoken audio + ASR transcription — there is
no silent text channel for ``[END_CALL]``. Models often vocalize English
\"end call\", which leaks into the LiveKit room. Helpers detect / strip both the
bracket token and spoken hang-up phrases so the bridge can mute leftover PCM.
"""

from __future__ import annotations

import re

END_CALL_TOKEN = "[END_CALL]"

# Spoken English forms Gemini often utters when asked to emit the harness token.
_SPOKEN_END_RE = re.compile(
    r"(?i)(?:\[\s*end[_\s\-]*call\s*\]|\bend[_\s\-]*call\b|\bhang[_\s\-]*up\b)[.!?]*"
)


def contains_end_call_signal(text: str) -> bool:
    if not text:
        return False
    if END_CALL_TOKEN in text:
        return True
    return _SPOKEN_END_RE.search(text) is not None


def strip_end_call_signal(text: str) -> str:
    if not text:
        return ""
    out = text.replace(END_CALL_TOKEN, " ")
    out = _SPOKEN_END_RE.sub(" ", out)
    # Collapse whitespace and stray punctuation left by the substitute.
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r"[,\s]+$", "", out)
    return " ".join(out.split()).strip()
