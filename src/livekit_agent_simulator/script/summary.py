"""Post-run caller behavior aggregates for summary.json / web."""

from __future__ import annotations

from typing import Any

def build_caller_behavior_summary(events: list[dict]) -> dict[str, Any]:
    """Aggregate barge / silence / recovery stats for summary.json + report player.

    Always safe to call; zeros when the run had no scripted caller behavior.
    """
    cues = [e for e in events if e.get("kind") == "sim.script.cue"]
    waits = [e for e in events if e.get("kind") == "sim.script.wait"]
    from .models import counts_for_recovery_barge

    barges = []
    by_class: dict[str, int] = {}
    for e in cues:
        spec = e.get("spec") or {}
        cls = str(spec.get("class") or spec.get("interrupt_class") or "") or None
        if cls:
            by_class[cls] = by_class.get(cls, 0) + 1
        if counts_for_recovery_barge(
            barge_in=bool(spec.get("barge_in")),
            interrupt_class=cls,
        ):
            barges.append(e)
    barges_during = [
        e for e in barges if (e.get("spec") or {}).get("during_agent_speech")
    ]
    cues_during = [
        e for e in cues if (e.get("spec") or {}).get("during_agent_speech")
    ]
    silence_events = [e for e in events if e.get("kind") == "silence.detected"]
    interruptions = [e for e in events if e.get("kind") == "interruption"]
    agent_finals = [e for e in events if e.get("kind") == "transcript.agent.final"]

    barge_ms = barges[0].get("ts_mono_ms") if barges else None
    silence_ms = waits[0].get("ts_mono_ms") if waits else None

    agent_after_barge = 0
    recovery_ms: int | None = None
    if barge_ms is not None:
        after = [
            int(e.get("ts_mono_ms") or 0)
            for e in agent_finals
            if int(e.get("ts_mono_ms") or 0) > int(barge_ms)
        ]
        agent_after_barge = len(after)
        if after:
            recovery_ms = after[0] - int(barge_ms)

    agent_after_silence = 0
    if silence_ms is not None:
        agent_after_silence = sum(
            1
            for e in agent_finals
            if int(e.get("ts_mono_ms") or 0) >= int(silence_ms)
        )

    assets: list[str] = []
    for e in events:
        if e.get("kind") not in ("sim.script.cue", "sim.script_inject"):
            continue
        a = (e.get("spec") or {}).get("asset")
        if a and str(a) not in assets:
            assets.append(str(a))

    return {
        "script_cues_fired": len(cues),
        "waits_fired": len(waits),
        "barges_fired": len(barges),
        "barges_during_agent": len(barges_during),
        "cues_during_agent": len(cues_during),
        "silences_held": len(waits),
        "silence_events": len(silence_events),
        "interruptions": len(interruptions),
        "agent_finals_after_barge": agent_after_barge,
        "agent_finals_after_silence": agent_after_silence,
        "recovery_ms": recovery_ms,
        "cue_assets": assets,
        "by_class": by_class,
    }


