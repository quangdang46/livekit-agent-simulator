"""Voice metrics aggregates from the forensic event stream (P1.3).

Black-box definitions (what we can measure without agent internals):

| Metric | Definition (lk-sim) | Industry analog |
|---|---|---|
| ``turn_taking_ms`` | user.final → first agent.final in that turn | Turn latency / TTFA per turn |
| ``ttfw_ms`` | run start → first agent speech (final or preamble) | Call-level Time to First Word |
| ``recovery_ms`` | first barge/interruption → next agent.final | Barge-in recovery latency |
| ``barge_recovery_rate`` | barges with ≥1 agent.final after / barges | Barge-in recovery rate |
| ``talk_ratio`` | agent transcript chars / (agent+user) | Talk-to-listen proxy |

Percentiles use the same nearest-rank helper as ``EventWriter``.
Targets (Hamming production, cascading STT→LLM→TTS + telephony, 4M+ calls):
  turn P50 ~1.5–1.7s, P95 ~3.5–5s; TTFW aspirational <800ms (often higher e2e).
"""

from __future__ import annotations

from typing import Any


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round((pct / 100.0) * (len(values) - 1))))
    return float(values[idx])


def _pct_block(samples: list[float]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "p50": _percentile(samples, 50),
        "p95": _percentile(samples, 95),
        "p99": _percentile(samples, 99),
        "max": max(samples) if samples else None,
        "min": min(samples) if samples else None,
        "mean": (sum(samples) / len(samples)) if samples else None,
    }


def _mono(e: dict[str, Any]) -> int:
    try:
        return int(e.get("ts_mono_ms") or 0)
    except (TypeError, ValueError):
        return 0


def _is_barge_event(e: dict[str, Any]) -> bool:
    """True for recovery-relevant barges only (correction/escalate; not backchannel/noise)."""
    from .script.models import counts_for_recovery_barge

    kind = str(e.get("kind") or "")
    spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}
    cls = spec.get("class") or spec.get("interrupt_class")
    if kind == "sim.script.cue" and spec.get("barge_in"):
        return counts_for_recovery_barge(
            barge_in=True, interrupt_class=str(cls) if cls else None
        )
    if kind == "interruption" and (
        spec.get("barge_in") or str(spec.get("by") or "") == "sim"
    ):
        # Explicit false_positive / non-recovery classes do not count.
        if str(spec.get("class") or "") in ("noise", "backchannel", "dtmf", "silence"):
            return False
        if spec.get("false_positive"):
            return False
        return counts_for_recovery_barge(
            barge_in=True, interrupt_class=str(cls) if cls else "correction"
        )
    return False


def compute_voice_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive voice QA metrics from ``events.jsonl`` envelopes.

    Safe on empty/partial runs — returns nulls/zeros rather than raising.
    """
    turn_taking: list[float] = []
    agent_final_ms: list[int] = []
    user_final_ms: list[int] = []
    agent_chars = 0
    user_chars = 0
    tool_starts = 0
    tool_errors = 0
    interruptions = 0
    silence_events = 0
    barge_ms: list[int] = []
    ttfw_ms: int | None = None
    first_agent_kind: str | None = None

    for e in events:
        kind = str(e.get("kind") or "")
        spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}
        mono = _mono(e)

        if kind == "transcript.agent.final":
            agent_final_ms.append(mono)
            text = str(spec.get("text") or "")
            agent_chars += len(text.strip())
            ttm = spec.get("turn_taking_ms")
            if ttm is not None:
                try:
                    turn_taking.append(float(ttm))
                except (TypeError, ValueError):
                    pass
            if ttfw_ms is None:
                ttfw_ms = mono
                first_agent_kind = "transcript.agent.final"
        elif kind == "transcript.agent.preamble":
            if ttfw_ms is None:
                ttfw_ms = mono
                first_agent_kind = "transcript.agent.preamble"
            text = str(spec.get("text") or "")
            agent_chars += len(text.strip())
        elif kind == "transcript.user.final":
            user_final_ms.append(mono)
            text = str(spec.get("text") or "")
            user_chars += len(text.strip())
        elif kind == "tool.start":
            tool_starts += 1
        elif kind == "tool.error":
            tool_errors += 1
        elif kind == "interruption":
            interruptions += 1
        elif kind == "silence.detected":
            silence_events += 1

        if _is_barge_event(e):
            barge_ms.append(mono)

    # Dedup barge timestamps (cue + interruption often fire together)
    barge_unique = sorted(set(barge_ms))
    recovery_samples: list[float] = []
    barges_recovered = 0
    for b in barge_unique:
        nxt = next((a for a in agent_final_ms if a > b), None)
        if nxt is not None:
            barges_recovered += 1
            recovery_samples.append(float(nxt - b))

    barge_count = len(barge_unique)
    recovery_rate: float | None
    if barge_count == 0:
        recovery_rate = None
    else:
        recovery_rate = barges_recovered / barge_count

    total_chars = agent_chars + user_chars
    talk_ratio: float | None
    if total_chars == 0:
        talk_ratio = None
    else:
        talk_ratio = agent_chars / total_chars

    slow_turns_2500 = sum(1 for v in turn_taking if v > 2500)
    slow_turns_5000 = sum(1 for v in turn_taking if v > 5000)

    return {
        "schema": "agent-sim/metrics/v1",
        "turn_taking_ms": _pct_block(turn_taking),
        "ttfw_ms": ttfw_ms,
        "ttfw_source": first_agent_kind,
        "recovery_ms": _pct_block(recovery_samples),
        "barge_count": barge_count,
        "barges_recovered": barges_recovered,
        "barge_recovery_rate": recovery_rate,
        "interruption_count": interruptions,
        "silence_events": silence_events,
        "agent_finals": len(agent_final_ms),
        "user_finals": len(user_final_ms),
        "tool_calls": tool_starts,
        "tool_errors": tool_errors,
        "tool_error_rate": (tool_errors / tool_starts) if tool_starts else None,
        "talk_ratio": talk_ratio,
        "agent_chars": agent_chars,
        "user_chars": user_chars,
        "slow_turns_over_2500ms": slow_turns_2500,
        "slow_turns_over_5000ms": slow_turns_5000,
    }


def metrics_digest(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Flat fields for suite matrix / compare_runs."""
    if not isinstance(metrics, dict):
        return {
            "ttfw_ms": None,
            "turn_p50_ms": None,
            "turn_p95_ms": None,
            "recovery_p50_ms": None,
            "barge_count": None,
            "barge_recovery_rate": None,
            "talk_ratio": None,
        }
    tt = metrics.get("turn_taking_ms") if isinstance(metrics.get("turn_taking_ms"), dict) else {}
    rec = metrics.get("recovery_ms") if isinstance(metrics.get("recovery_ms"), dict) else {}
    return {
        "ttfw_ms": metrics.get("ttfw_ms"),
        "turn_p50_ms": tt.get("p50"),
        "turn_p95_ms": tt.get("p95"),
        "recovery_p50_ms": rec.get("p50"),
        "barge_count": metrics.get("barge_count"),
        "barge_recovery_rate": metrics.get("barge_recovery_rate"),
        "talk_ratio": metrics.get("talk_ratio"),
    }
