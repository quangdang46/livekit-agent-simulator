"""Player cue dedupe: ASR typo tolerance + dual transcript sources."""

from __future__ import annotations

from livekit_agent_simulator.web.cue_helpers.source_priority import (
    source_rank,
    texts_similar,
)
from livekit_agent_simulator.web.transcript_cues import _build_transcript_cues


def test_texts_similar_asr_typos_okay_thanks():
    assert texts_similar("Okey. Thank's.", "Okay. Thanks.")
    assert texts_similar("Okay, thanks. Bye.", "Okay. Thanks.")
    assert not texts_similar("What's the monthly fee?", "Okay. Thanks.")


def test_source_rank_prefers_data_topic_over_lk_for_user():
    assert source_rank("app.transcript", "user") < source_rank("lk.transcription", "user")


def test_build_transcript_cues_collapses_dual_stt_bye():
    t0 = 8000
    events = [
        {
            "kind": "transcript.user.final",
            "source": "lk.transcription",
            "ts_mono_ms": t0 + 92200,
            "spec": {"text": "Okey. Thank's."},
        },
        {
            "kind": "transcript.user.final",
            "source": "app.transcript",
            "ts_mono_ms": t0 + 93100,
            "spec": {"text": "Okay. Thanks."},
        },
    ]
    cues = _build_transcript_cues(events, t0=t0, duration_ms=94000)
    user = [c for c in cues if c["role"] == "user"]
    assert len(user) == 1
    # Prefer opaque data-topic source (cleaner) over LK ASR garble when ranks differ.
    assert "Okay" in user[0]["text"] or "okay" in user[0]["text"].lower()


def test_build_transcript_cues_collapses_late_agent_stt_of_gemini_user():
    """Agent data-topic STT of the caller often lands ~5–8s after sim.gemini final."""
    t0 = 8421
    events = [
        {
            "kind": "transcript.user.final",
            "source": "sim.gemini",
            "ts_mono_ms": t0 + 6188,
            "spec": {
                "text": "Um, hi. I'm calling because I think I want to sign up for your basic plan."
            },
        },
        {
            "kind": "transcript.user.final",
            "source": "app.transcript",
            "ts_mono_ms": t0 + 12985,
            "spec": {
                "text": "Um, hi, I'm calling because I think I want to sign up for your basic plan."
            },
        },
        {
            "kind": "transcript.user.final",
            "source": "sim.gemini",
            "ts_mono_ms": t0 + 59968,
            "spec": {
                "text": (
                    "Oh, okay. Um, I guess I can check later then. "
                    "What do I need to do to sign up? My name is Mai Nguyen."
                )
            },
        },
        {
            "kind": "transcript.user.final",
            "source": "app.transcript",
            "ts_mono_ms": t0 + 63079,
            "spec": {"text": "My name is Mai Nguyen."},
        },
    ]
    cues = _build_transcript_cues(events, t0=t0, duration_ms=90613)
    user = [c for c in cues if c["role"] == "user"]
    assert len(user) == 2
    assert all(c.get("source") == "sim.gemini" for c in user)
    assert "sign up" in user[0]["text"].lower()
    assert "mai nguyen" in user[1]["text"].lower()
