"""Deterministic scenario asserts (tools, transcript phrases, lightweight outcomes).

Portable: no consumer-specific tool names baked into core — scenarios declare expects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .metrics import compute_voice_metrics


@dataclass(frozen=True)
class ToolExpect:
    name: str
    min_count: int = 1
    max_count: int | None = None
    # Subset match against tool.start spec.payload (or nested args/arguments).
    args_contains: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptExpect:
    role: str = "agent"  # agent | user | any
    contains_any: tuple[str, ...] = ()
    must_not_match: str | None = None  # regex


@dataclass(frozen=True)
class OutcomeExpect:
    """Post-run outcome check.

    type:
      - transcript_contains: any agent/user text matches phrases (contains_any)
      - llm_bool: deferred to judge layer (evaluated when judge runs)
      - recovery: agent re-engages after sim barge-in / interruption
      - latency: hard gates on turn_taking / TTFW / recovery percentiles (P1.3)
      - ended_by: assert which side ended the call (sim | agent | detect)
    """

    id: str
    type: str  # transcript_contains | llm_bool | recovery | latency | ended_by
    phrases: tuple[str, ...] = ()
    prompt: str | None = None  # for llm_bool
    role: str = "any"
    min_agent_finals_after_barge_in: int = 1
    min_interruptions: int = 0
    max_ms_after_barge_to_agent_final: int | None = None
    # latency thresholds (omit = do not check that dimension)
    max_turn_p50_ms: int | None = None
    max_turn_p95_ms: int | None = None
    max_turn_p99_ms: int | None = None
    max_turn_max_ms: int | None = None
    max_ttfw_ms: int | None = None
    max_recovery_p50_ms: int | None = None
    max_recovery_p95_ms: int | None = None
    min_barge_recovery_rate: float | None = None
    require_turn_samples: int = 0
    # ended_by: who must hang up first
    ended_by: str | None = None  # "sim" | "agent"


@dataclass(frozen=True)
class SipExpect:
    """SIP-related hard asserts (portable — no carrier-specific fields)."""

    # Require sip.participant_connected (or outbound/inbound answered) in events.
    participant_present: bool = False
    # Expected sip.callStatus value(s), e.g. ("active",). Empty = do not check status.
    call_status_any: tuple[str, ...] = ()
    # Require outbound.dial_answered or inbound.answered.
    dial_answered: bool = False


@dataclass
class AssertSpec:
    tools: list[ToolExpect] = field(default_factory=list)
    transcript: list[TranscriptExpect] = field(default_factory=list)
    outcomes: list[OutcomeExpect] = field(default_factory=list)
    sip: SipExpect | None = None

    @property
    def empty(self) -> bool:
        return not (self.tools or self.transcript or self.outcomes or self.sip)


def _opt_int(raw: dict[str, Any], key: str) -> int | None:
    if raw.get(key) is None:
        return None
    return int(raw[key])


def _opt_float(raw: dict[str, Any], key: str) -> float | None:
    if raw.get(key) is None:
        return None
    return float(raw[key])


def parse_assert_spec(spec: dict[str, Any], path_label: str = "Assert") -> AssertSpec:
    tools: list[ToolExpect] = []
    for i, raw in enumerate(spec.get("tools") or []):
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError(f"{path_label}: tools[{i}] needs name")
        args = raw.get("args_contains") or raw.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"{path_label}: tools[{i}].args_contains must be object")
        tools.append(
            ToolExpect(
                name=str(raw["name"]).strip(),
                min_count=int(raw.get("min_count", 1)),
                max_count=int(raw["max_count"]) if raw.get("max_count") is not None else None,
                args_contains=dict(args),
            )
        )

    transcript: list[TranscriptExpect] = []
    for i, raw in enumerate(spec.get("transcript") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path_label}: transcript[{i}] must be object")
        contains = raw.get("contains_any") or raw.get("contains") or []
        if isinstance(contains, str):
            contains = [contains]
        if not isinstance(contains, list):
            raise ValueError(f"{path_label}: transcript[{i}].contains_any must be array/string")
        transcript.append(
            TranscriptExpect(
                role=str(raw.get("role", "agent")),
                contains_any=tuple(str(x) for x in contains),
                must_not_match=str(raw["must_not_match"]) if raw.get("must_not_match") else None,
            )
        )

    outcomes: list[OutcomeExpect] = []
    for i, raw in enumerate(spec.get("outcomes") or []):
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ValueError(f"{path_label}: outcomes[{i}] needs id")
        otype = str(raw.get("type", "transcript_contains"))
        if otype not in ("transcript_contains", "llm_bool", "recovery", "latency", "ended_by"):
            raise ValueError(f"{path_label}: outcomes[{i}].type unsupported: {otype}")
        phrases = raw.get("phrases") or raw.get("contains_any") or []
        if isinstance(phrases, str):
            phrases = [phrases]
        max_ms = raw.get("max_ms_after_barge_to_agent_final")
        if otype == "latency":
            has_gate = any(
                raw.get(k) is not None
                for k in (
                    "max_turn_p50_ms",
                    "max_turn_p95_ms",
                    "max_turn_p99_ms",
                    "max_turn_max_ms",
                    "max_ttfw_ms",
                    "max_recovery_p50_ms",
                    "max_recovery_p95_ms",
                    "min_barge_recovery_rate",
                )
            )
            if not has_gate:
                raise ValueError(
                    f"{path_label}: outcomes[{i}] latency needs at least one threshold "
                    "(max_turn_p50_ms / max_turn_p95_ms / max_ttfw_ms / …)"
                )
        eb = None
        if otype == "ended_by":
            eb = str(raw.get("ended_by") or raw.get("who") or "detect")
            if eb not in ("sim", "agent", "detect"):
                raise ValueError(
                    f"{path_label}: outcomes[{i}] ended_by must be 'sim' | 'agent' | 'detect'"
                )
        outcomes.append(
            OutcomeExpect(
                id=str(raw["id"]),
                type=otype,
                phrases=tuple(str(p) for p in phrases),
                prompt=str(raw["prompt"]) if raw.get("prompt") else None,
                role=str(raw.get("role", "any")),
                min_agent_finals_after_barge_in=int(
                    raw.get("min_agent_finals_after_barge_in", 1)
                ),
                min_interruptions=int(raw.get("min_interruptions", 0)),
                max_ms_after_barge_to_agent_final=int(max_ms) if max_ms is not None else None,
                max_turn_p50_ms=_opt_int(raw, "max_turn_p50_ms"),
                max_turn_p95_ms=_opt_int(raw, "max_turn_p95_ms"),
                max_turn_p99_ms=_opt_int(raw, "max_turn_p99_ms"),
                max_turn_max_ms=_opt_int(raw, "max_turn_max_ms"),
                max_ttfw_ms=_opt_int(raw, "max_ttfw_ms"),
                max_recovery_p50_ms=_opt_int(raw, "max_recovery_p50_ms"),
                max_recovery_p95_ms=_opt_int(raw, "max_recovery_p95_ms"),
                min_barge_recovery_rate=_opt_float(raw, "min_barge_recovery_rate"),
                require_turn_samples=int(raw.get("require_turn_samples", 0) or 0),
                ended_by=eb or (raw.get("ended_by") or raw.get("who")),
            )
        )

    sip: SipExpect | None = None
    sip_raw = spec.get("sip")
    if isinstance(sip_raw, dict):
        statuses = sip_raw.get("call_status_any") or sip_raw.get("call_status") or []
        if isinstance(statuses, str):
            statuses = [statuses]
        if not isinstance(statuses, list):
            raise ValueError(f"{path_label}: sip.call_status_any must be string or array")
        sip = SipExpect(
            participant_present=bool(
                sip_raw.get("participant_present", sip_raw.get("sip_participant_present", False))
            ),
            call_status_any=tuple(str(s) for s in statuses),
            dial_answered=bool(sip_raw.get("dial_answered", False)),
        )

    return AssertSpec(tools=tools, transcript=transcript, outcomes=outcomes, sip=sip)


def _tool_args_blob(spec: dict[str, Any]) -> dict[str, Any]:
    payload = spec.get("payload")
    if isinstance(payload, dict):
        for key in ("args", "arguments", "input", "params"):
            if isinstance(payload.get(key), dict):
                return payload[key]
        return payload
    for key in ("args", "arguments"):
        if isinstance(spec.get(key), dict):
            return spec[key]
    return {}


def _dict_contains(hay: dict[str, Any], needle: dict[str, Any]) -> bool:
    for k, v in needle.items():
        if k not in hay:
            return False
        if isinstance(v, dict) and isinstance(hay[k], dict):
            if not _dict_contains(hay[k], v):
                return False
        elif hay[k] != v and str(hay[k]) != str(v):
            return False
    return True


def _transcript_texts(events: list[dict[str, Any]], role: str) -> list[str]:
    texts: list[str] = []
    for e in events:
        kind = str(e.get("kind") or "")
        if not kind.startswith("transcript.") or not kind.endswith(".final"):
            continue
        if role == "agent" and "agent" not in kind:
            continue
        if role == "user" and "user" not in kind:
            continue
        t = (e.get("spec") or {}).get("text")
        if isinstance(t, str) and t.strip():
            texts.append(t.strip())
    return texts


def evaluate_asserts(events: list[dict[str, Any]], asserts: AssertSpec | None) -> dict[str, Any]:
    """Deterministic checks only (llm_bool outcomes marked pending)."""
    if asserts is None or asserts.empty:
        return {"pass": True, "skipped": True, "checks": []}

    checks: list[dict[str, Any]] = []
    tool_starts = [e for e in events if e.get("kind") == "tool.start"]

    if asserts.sip is not None:
        checks.extend(_eval_sip_expect(asserts.sip, events))

    for te in asserts.tools:
        matches = []
        for e in tool_starts:
            spec = e.get("spec") or {}
            name = str(spec.get("name") or "")
            if name != te.name:
                continue
            if te.args_contains:
                args = _tool_args_blob(spec)
                if not _dict_contains(args, te.args_contains):
                    continue
            matches.append(e)
        n = len(matches)
        ok = n >= te.min_count and (te.max_count is None or n <= te.max_count)
        checks.append(
            {
                "check": f"tool:{te.name}",
                "pass": ok,
                "expected_min": te.min_count,
                "expected_max": te.max_count,
                "actual": n,
                "args_contains": te.args_contains or None,
            }
        )

    for i, tr in enumerate(asserts.transcript):
        role = tr.role if tr.role in ("agent", "user") else "any"
        if role == "any":
            texts = _transcript_texts(events, "agent") + _transcript_texts(events, "user")
        else:
            texts = _transcript_texts(events, role)
        blob = "\n".join(texts)
        ok = True
        reason = None
        if tr.contains_any:
            ok = any(p.lower() in blob.lower() for p in tr.contains_any)
            if not ok:
                reason = f"none of {list(tr.contains_any)} found in {role} transcript"
        if ok and tr.must_not_match:
            if re.search(tr.must_not_match, blob, re.I):
                ok = False
                reason = f"matched forbidden pattern {tr.must_not_match!r}"
        checks.append(
            {
                "check": f"transcript[{i}]",
                "pass": ok,
                "role": tr.role,
                "reason": reason,
            }
        )

    pending_llm: list[dict[str, Any]] = []
    barge_ms: list[int] = []
    for e in events:
        kind = str(e.get("kind") or "")
        spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}
        try:
            mono = int(e.get("ts_mono_ms") or 0)
        except (TypeError, ValueError):
            mono = 0
        if kind == "sim.script.cue" and spec.get("barge_in"):
            barge_ms.append(mono)
        if kind == "interruption" and (
            spec.get("barge_in") or str(spec.get("by") or "") == "sim"
        ):
            barge_ms.append(mono)
    barge_ms = sorted(set(barge_ms))
    agent_final_ms: list[int] = []
    for e in events:
        if e.get("kind") != "transcript.agent.final":
            continue
        try:
            agent_final_ms.append(int(e.get("ts_mono_ms") or 0))
        except (TypeError, ValueError):
            continue
    interruptions = [e for e in events if e.get("kind") == "interruption"]

    for oc in asserts.outcomes:
        if oc.type == "transcript_contains":
            role = oc.role if oc.role in ("agent", "user") else "any"
            if role == "any":
                texts = _transcript_texts(events, "agent") + _transcript_texts(events, "user")
            else:
                texts = _transcript_texts(events, role)
            blob = "\n".join(texts)
            ok = bool(oc.phrases) and any(p.lower() in blob.lower() for p in oc.phrases)
            checks.append(
                {
                    "check": f"outcome:{oc.id}",
                    "pass": ok,
                    "type": oc.type,
                    "phrases": list(oc.phrases),
                }
            )
        elif oc.type == "recovery":
            after = 0
            first_barge = barge_ms[0] if barge_ms else None
            if first_barge is not None:
                after = sum(1 for t in agent_final_ms if t > first_barge)
            ok = after >= oc.min_agent_finals_after_barge_in
            if oc.min_interruptions and len(interruptions) < oc.min_interruptions:
                ok = False
            timing_ok = True
            recovery_ms = None
            if ok and oc.max_ms_after_barge_to_agent_final is not None and first_barge is not None:
                nxt = next((t for t in agent_final_ms if t > first_barge), None)
                if nxt is None:
                    timing_ok = False
                else:
                    recovery_ms = nxt - first_barge
                    timing_ok = recovery_ms <= oc.max_ms_after_barge_to_agent_final
                ok = ok and timing_ok
            checks.append(
                {
                    "check": f"outcome:{oc.id}",
                    "pass": ok,
                    "type": "recovery",
                    "agent_finals_after_barge_in": after,
                    "expected_min": oc.min_agent_finals_after_barge_in,
                    "interruptions": len(interruptions),
                    "recovery_ms": recovery_ms,
                    "max_ms_after_barge_to_agent_final": oc.max_ms_after_barge_to_agent_final,
                }
            )
        elif oc.type == "latency":
            checks.append(_eval_latency_outcome(oc, events))
        elif oc.type == "ended_by":
            checks.append(_eval_ended_by_outcome(oc, events))
        elif oc.type == "llm_bool":
            pending_llm.append({"id": oc.id, "prompt": oc.prompt or oc.id})
            checks.append(
                {
                    "check": f"outcome:{oc.id}",
                    "pass": True,  # does not fail hard assert; judge layer decides
                    "type": "llm_bool",
                    "pending_judge": True,
                    "prompt": oc.prompt,
                }
            )

    hard = [c for c in checks if not c.get("pending_judge")]
    return {
        "pass": all(bool(c.get("pass")) for c in hard) if hard else True,
        "skipped": False,
        "checks": checks,
        "pending_llm_outcomes": pending_llm,
    }

def _eval_latency_outcome(oc: OutcomeExpect, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard gate on turn_taking / TTFW / recovery percentiles from event stream."""
    m = compute_voice_metrics(events)
    tt = m.get("turn_taking_ms") if isinstance(m.get("turn_taking_ms"), dict) else {}
    rec = m.get("recovery_ms") if isinstance(m.get("recovery_ms"), dict) else {}
    reasons: list[str] = []
    ok = True

    n_turns = int(tt.get("count") or 0)
    if oc.require_turn_samples and n_turns < oc.require_turn_samples:
        ok = False
        reasons.append(
            f"turn samples {n_turns} < require_turn_samples {oc.require_turn_samples}"
        )

    def _gate(actual: Any, limit: int | None, label: str) -> None:
        nonlocal ok
        if limit is None:
            return
        if actual is None:
            ok = False
            reasons.append(f"{label}: no sample (need measured value ≤ {limit}ms)")
            return
        try:
            val = float(actual)
        except (TypeError, ValueError):
            ok = False
            reasons.append(f"{label}: invalid actual {actual!r}")
            return
        if val > limit:
            ok = False
            reasons.append(f"{label} {val:.0f}ms > max {limit}ms")

    _gate(tt.get("p50"), oc.max_turn_p50_ms, "turn_p50")
    _gate(tt.get("p95"), oc.max_turn_p95_ms, "turn_p95")
    _gate(tt.get("p99"), oc.max_turn_p99_ms, "turn_p99")
    _gate(tt.get("max"), oc.max_turn_max_ms, "turn_max")
    _gate(m.get("ttfw_ms"), oc.max_ttfw_ms, "ttfw")
    _gate(rec.get("p50"), oc.max_recovery_p50_ms, "recovery_p50")
    _gate(rec.get("p95"), oc.max_recovery_p95_ms, "recovery_p95")

    if oc.min_barge_recovery_rate is not None:
        rate = m.get("barge_recovery_rate")
        barges = int(m.get("barge_count") or 0)
        if barges == 0:
            ok = False
            reasons.append(
                f"barge_recovery_rate: no barges fired "
                f"(need rate >= {oc.min_barge_recovery_rate})"
            )
        elif rate is None or float(rate) < float(oc.min_barge_recovery_rate):
            ok = False
            reasons.append(
                f"barge_recovery_rate {rate} < min {oc.min_barge_recovery_rate}"
            )

    return {
        "check": f"outcome:{oc.id}",
        "pass": ok,
        "type": "latency",
        "reasons": reasons,
        "actual": {
            "turn_p50_ms": tt.get("p50"),
            "turn_p95_ms": tt.get("p95"),
            "turn_p99_ms": tt.get("p99"),
            "turn_max_ms": tt.get("max"),
            "turn_count": n_turns,
            "ttfw_ms": m.get("ttfw_ms"),
            "recovery_p50_ms": rec.get("p50"),
            "recovery_p95_ms": rec.get("p95"),
            "barge_count": m.get("barge_count"),
            "barge_recovery_rate": m.get("barge_recovery_rate"),
        },
        "limits": {
            "max_turn_p50_ms": oc.max_turn_p50_ms,
            "max_turn_p95_ms": oc.max_turn_p95_ms,
            "max_turn_p99_ms": oc.max_turn_p99_ms,
            "max_turn_max_ms": oc.max_turn_max_ms,
            "max_ttfw_ms": oc.max_ttfw_ms,
            "max_recovery_p50_ms": oc.max_recovery_p50_ms,
            "max_recovery_p95_ms": oc.max_recovery_p95_ms,
            "min_barge_recovery_rate": oc.min_barge_recovery_rate,
            "require_turn_samples": oc.require_turn_samples or None,
        },
    }


def _eval_ended_by_outcome(oc: OutcomeExpect, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Assert that the call ended by the expected side (sim | agent | detect)."""
    sim_hangup = [e for e in events if e.get("kind") in ("sim.hang_up", "sim.script.hang_up")]
    end_cond = [e for e in events if e.get("kind") == "run.end_condition"]

    who = "detect"
    reason_parts: list[str] = []

    if sim_hangup:
        who = "sim"
        reason_parts.append("sim_hang_up event (via script)")
    elif end_cond:
        er = end_cond[-1].get("spec", {}).get("reason", "")
        er_s = str(er) if er else ""
        if "sim_end_call" in er_s:
            who = "sim"
            reason_parts.append(f"end_reason: {er_s}")
        elif er_s in ("agent_disconnected", "dead_call_silence"):
            who = "agent"
            reason_parts.append(f"end_reason: {er_s}")
        elif er_s in ("max_turns", "timeout"):
            reason_parts.append(f"end_reason: {er_s} (no hang-up side)")
    else:
        for e in events:
            if e.get("kind") == "sim.end_call_token":
                who = "sim"
                reason_parts.append("sim end_call_token")
                break

    ok = True
    reasons: list[str] = []
    if oc.ended_by is not None and oc.ended_by != "detect":
        if who != oc.ended_by:
            ok = False
            reasons.append(f"expected ended_by={oc.ended_by}, detected={who}")

    return {
        "check": f"outcome:{oc.id}",
        "pass": ok,
        "type": "ended_by",
        "expected": oc.ended_by,
        "actual": who,
        "reasons": reasons,
        "details": ", ".join(reason_parts) if reason_parts else None,
    }

def _eval_sip_expect(sip: SipExpect, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate portable SIP asserts from forensic events."""
    checks: list[dict[str, Any]] = []
    kinds = [str(e.get("kind") or "") for e in events]
    present = any(
        k in (
            "sip.participant_connected",
            "outbound.dial_answered",
            "inbound.answered",
        )
        for k in kinds
    )
    if sip.participant_present:
        checks.append(
            {
                "check": "sip_participant_present",
                "pass": present,
                "type": "sip",
                "actual": present,
            }
        )
    if sip.dial_answered:
        answered = any(k in ("outbound.dial_answered", "inbound.answered") for k in kinds)
        checks.append(
            {
                "check": "sip_dial_answered",
                "pass": answered,
                "type": "sip",
                "actual": answered,
            }
        )
    if sip.call_status_any:
        statuses: list[str] = []
        for e in events:
            if e.get("kind") != "sip.call_status":
                continue
            spec = e.get("spec") if isinstance(e.get("spec"), dict) else {}
            st = spec.get("status") or spec.get("call_status")
            if st:
                statuses.append(str(st))
        # Also treat dial_answered as active for hairpin paths that skip attribute polling.
        if any(k in ("outbound.dial_answered", "inbound.answered") for k in kinds):
            statuses.append("active")
        ok = any(s in sip.call_status_any for s in statuses)
        checks.append(
            {
                "check": "sip_call_status",
                "pass": ok,
                "type": "sip",
                "expected_any": list(sip.call_status_any),
                "actual": statuses,
            }
        )
    return checks
