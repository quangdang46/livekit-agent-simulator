"""Source ranking and ghost-STT filtering for player cue building."""

from __future__ import annotations

import re
from typing import Any

# Built-in lk-sim / LiveKit sources only. Target data topics are ranked generically
# below (any non-builtin source beats raw lk.transcription for user).
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

# Common ASR / TTS misspellings that should still collapse as one utterance.
_ASR_TOKEN_ALIASES = {
    "okey": "okay",
    "ok": "okay",
    "k": "okay",
    "thank": "thanks",
    "thx": "thanks",
    "bye": "bye",
    "byebye": "bye",
}


def source_rank(source: str | None, role: str) -> int:
    s = (source or "").strip()
    table = _USER_SOURCE_RANK if role == "user" else _AGENT_SOURCE_RANK
    if s in table:
        return table[s]
    if s and s not in ("sim.gemini", "lk.transcription"):
        # Opaque target data-topic sources (observe.data_topics) — prefer over raw LK STT.
        return 1 if role == "user" else 0
    return 9


def _normalize_for_similarity(text: str) -> str:
    t = text.lower().strip()
    t = t.replace("'", "").replace("'", "").replace("'", "")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _canonical_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for tok in _normalize_for_similarity(text).split():
        if not tok:
            continue
        out.add(_ASR_TOKEN_ALIASES.get(tok, tok))
    return out


def texts_similar(a: str, b: str) -> bool:
    """True when two transcripts likely describe the same spoken utterance.

    Tolerates ASR typos (Okey/Okay, Thank's/Thanks) and punctuation drift so
    dual pipelines (lk.transcription + target data topic) collapse in the player.
    """
    a_n = _normalize_for_similarity(a)
    b_n = _normalize_for_similarity(b)
    if not a_n or not b_n:
        return False
    if a_n == b_n or a_n in b_n or b_n in a_n:
        return True
    ta, tb = _canonical_tokens(a), _canonical_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter >= max(1, int(min(len(ta), len(tb)) * 0.5 + 0.5))
