"""Promote a finished run into a draft scenario JSONL (fail → golden, P1.4/P2 #34).

Reads ``reports/<run_id>/{meta,summary,events}`` and synthesizes an agent-sim/v1
draft. Dispatch metadata is copied from the original scenario file when still
present on disk; otherwise the draft omits Dispatch and notes that in Context.

Extract quality rules (issue #34):
- Persona.brief is a short mission statement — never a transcript paste.
- Caller intent lands in ``goals[]`` (source persona goals preferred, else
  intent-phrased from the first user finals) + ``constraints[]``.
- One ``Behavior`` barge/noise stub is reconstructed from ``sim.script.cue``
  markers in events.jsonl so a barge-fail replays deterministically.
- When ``first_speaker=user``, also emit a minimal Script **open** line (source
  Script open preferred, else first user final). Behavior barge-only would
  otherwise suppress the Gemini bootstrap and dead-air the call.
- Transcript sample + metrics hints live in ``Context.notes`` (author-only).
- No full Script reverse-engineer. Humans/agents must review before CI promote.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def _redact(text: str) -> str:
    t = _EMAIL_RE.sub("[email]", text)
    t = _CARD_RE.sub("[card]", t)
    t = _PHONE_RE.sub("[phone]", t)
    return t


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _transcript_finals(events: list[dict[str, Any]], role: str) -> list[str]:
    texts: list[str] = []
    for e in events:
        kind = str(e.get("kind") or "")
        if kind != f"transcript.{role}.final":
            continue
        t = (e.get("spec") or {}).get("text")
        if isinstance(t, str) and t.strip():
            texts.append(_redact(t.strip()))
    return texts


def _slug_id(base: str, run_id: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", (base or "from-run").strip()).strip("-_")
    raw = (raw[:40] if raw else "from-run").lower()
    tail = run_id.split("-")[-1] if run_id else "draft"
    tail = re.sub(r"[^a-zA-Z0-9]", "", tail)[:6] or "draft"
    cand = f"from-{raw}-{tail}"
    return cand[:64]


def _dispatch_from_source_scenario(scenario_file: str | None) -> str | None:
    if not scenario_file:
        return None
    path = Path(scenario_file)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("kind") == "Dispatch":
            md = (obj.get("spec") or {}).get("metadata")
            if isinstance(md, str) and md.strip():
                return md.strip()
            if isinstance(md, dict):
                return json.dumps(md, ensure_ascii=False, separators=(",", ":"))
    return None


def _persona_from_source(scenario_file: str | None) -> dict[str, Any] | None:
    if not scenario_file:
        return None
    path = Path(scenario_file)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("kind") == "Persona" and isinstance(obj.get("spec"), dict):
            return dict(obj["spec"])
    return None


def _pass_criteria_from_source(scenario_file: str | None) -> list[str]:
    if not scenario_file:
        return []
    path = Path(scenario_file)
    if not path.is_file():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("kind") == "PassCriteria":
            crit = (obj.get("spec") or {}).get("criteria") or []
            if isinstance(crit, list):
                return [str(c) for c in crit if str(c).strip()]
    return []


def _behavior_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Reconstruct one Behavior barge/noise stub from run markers.

    Uses the first successful ``sim.script.cue`` that fired as a cut-in
    (``barge_in`` true, or class ``noise``/``backchannel`` during agent
    speech). Falls back to a generic barge stub when only an ``interruption``
    marker with ``by=sim`` exists.
    """
    fallback_interruption: dict[str, Any] | None = None
    for e in events:
        kind = str(e.get("kind") or "")
        spec = e.get("spec") or {}
        if not isinstance(spec, dict):
            continue
        if kind == "interruption" and spec.get("by") == "sim" and fallback_interruption is None:
            fallback_interruption = spec
            continue
        if kind != "sim.script.cue" or spec.get("error"):
            continue
        icls = str(spec.get("class") or "correction")
        barge = bool(spec.get("barge_in"))
        during = bool(spec.get("during_agent_speech"))
        if not barge and not (icls in ("noise", "backchannel") and during):
            continue
        after_ms = max(600, int(spec.get("agent_active_ms") or 0) or 600)
        say = _redact(str(spec.get("say") or "").strip())
        asset = spec.get("asset")
        asset_s = str(asset).strip() if asset else None
        if icls == "noise":
            entry: dict[str, Any] = {
                "id": "replay-noise-1",
                "after_agent_ms": after_ms,
                "say": say or "[noise]",
            }
            if asset_s:
                entry["asset"] = asset_s
            return {"false_interrupts": [entry]}
        if icls == "backchannel" and not barge:
            entry = {
                "id": "replay-backchannel-1",
                "after_agent_ms": after_ms,
                "say": say or "uh-huh",
            }
            if asset_s:
                entry["asset"] = asset_s
            return {"backchannels": [entry]}
        entry = {
            "id": "replay-barge-1",
            "after_agent_ms": after_ms,
            "say": say or "Wait — one second —",
            "class": icls if icls in ("correction", "question", "urgent", "backchannel") else "correction",
        }
        if asset_s:
            entry["asset"] = asset_s
        return {"barge_ins": [entry]}
    if fallback_interruption is not None:
        icls = str(fallback_interruption.get("class") or "correction")
        return {
            "barge_ins": [
                {
                    "id": "replay-barge-1",
                    "after_agent_ms": 600,
                    "say": _redact(str(fallback_interruption.get("say") or "Wait — one second —")),
                    "class": icls if icls in ("correction", "question", "urgent", "backchannel") else "correction",
                }
            ]
        }
    return None


def _script_open_say_from_source(scenario_file: str | None) -> str | None:
    """First explicit Script speak open from the source scenario, if any."""
    if not scenario_file:
        return None
    path = Path(scenario_file)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("kind") != "Script":
            continue
        steps = (obj.get("spec") or {}).get("steps") or []
        if not isinstance(steps, list):
            return None
        for raw in steps:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action") or "speak").strip().lower()
            if action not in ("speak", ""):
                continue
            say = str(raw.get("say") or "").strip()
            if not say or say.startswith("["):
                continue
            # Prefer opens that do not require the agent to speak first.
            if raw.get("require_agent_spoke_first") is True:
                continue
            if bool(raw.get("barge_in")):
                continue
            return _redact(say)[:200]
        return None
    return None


def _script_open_for_user_first(
    *,
    first_speaker: str,
    user_texts: list[str],
    scenario_file: str | None,
) -> dict[str, Any] | None:
    """Minimal Script open so user-first + Behavior barge does not dead-air.

    ``DefaultCallerPolicy`` skips the speak-first bootstrap whenever any
    ``script_steps`` exist. Behavior barge compiles into script_steps, so a
    draft with only a Behavior cut-in would tell Gemini to wait for a cue that
    never fires (agent also waits for the caller). Emit one silence-triggered
    open line to break that deadlock.
    """
    if first_speaker != "user":
        return None
    say = _script_open_say_from_source(scenario_file)
    if not say and user_texts:
        say = _redact(user_texts[0])[:200]
    if not say:
        say = "Hi — I'm calling about my request."
    return {
        "steps": [
            {
                "id": "open",
                "trigger": "silence",
                "delay_ms": 2200,
                "say": say,
                "once": True,
                "require_agent_spoke_first": False,
            }
        ]
    }


def build_scenario_draft_from_run(
    report_dir: Path | str,
    *,
    scenario_id: str | None = None,
    locale_default: str = "en-US",
) -> dict[str, Any]:
    """Build a draft scenario dict from a report directory.

    Returns::
        {
          "scenario_id": str,
          "source_run_id": str,
          "jsonl": str,           # ready to write
          "kinds": list[str],
          "warnings": list[str],
          "notes": str,
        }
    """
    report_dir = Path(report_dir)
    meta_path = report_dir / "meta.json"
    summary_path = report_dir / "summary.json"
    events_path = report_dir / "events.jsonl"
    if not meta_path.exists() or not summary_path.exists():
        raise FileNotFoundError(
            f"Report incomplete under {report_dir}: need meta.json + summary.json"
        )

    meta = _load_json(meta_path)
    summary = _load_json(summary_path)
    events = _load_events(events_path)

    source_run_id = str(meta.get("run_id") or summary.get("run_id") or report_dir.name)
    source_scenario = str(meta.get("scenario_id") or "unknown")
    scenario_file = meta.get("scenario_file")
    if isinstance(scenario_file, str):
        scenario_file_s = scenario_file
    else:
        scenario_file_s = None

    sid = (scenario_id or "").strip() or _slug_id(source_scenario, source_run_id)
    if not _SCENARIO_ID_RE.match(sid):
        raise ValueError(
            f"Invalid scenario_id {sid!r}: use letters/digits/[_-], start with alnum, max 64"
        )

    run_spec = meta.get("run_spec") if isinstance(meta.get("run_spec"), dict) else {}
    max_turns = int(run_spec.get("max_turns") or summary.get("turn_count") or 6)
    timeout_s = int(run_spec.get("timeout_s") or 180)
    first_speaker = str(run_spec.get("first_speaker") or "agent")
    if first_speaker not in ("agent", "user"):
        first_speaker = "agent"

    # Locale: prefer original scenario metadata if parseable
    locale = locale_default
    if scenario_file_s and Path(scenario_file_s).is_file():
        for line in Path(scenario_file_s).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("//"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if obj.get("kind") == "Scenario":
                loc = (obj.get("metadata") or {}).get("locale")
                if loc:
                    locale = str(loc)
                break

    user_texts = _transcript_finals(events, "user")
    agent_texts = _transcript_finals(events, "agent")
    # de-dupe consecutive identical user finals (common with multi-source transcripts)
    deduped_user: list[str] = []
    for t in user_texts:
        if not deduped_user or deduped_user[-1] != t:
            deduped_user.append(t)
    user_texts = deduped_user

    src_persona = _persona_from_source(scenario_file_s) or {}
    name = str(src_persona.get("name") or "Caller")
    language = str(src_persona.get("language") or locale)
    traits = src_persona.get("traits") or ["polite"]
    if not isinstance(traits, list):
        traits = ["polite"]
    constraints = src_persona.get("constraints") or []
    if not isinstance(constraints, list):
        constraints = []

    # Goals: source persona goals win; else intent-phrased from user finals —
    # never a raw transcript dump (issue #34).
    src_goals = src_persona.get("goals")
    goals: list[str] = [str(g).strip() for g in src_goals if str(g).strip()] if isinstance(src_goals, list) else []
    if not goals:
        if user_texts:
            first = user_texts[0][:120] + ("…" if len(user_texts[0]) > 120 else "")
            goals.append(f'Open with the same request as the source run: "{first}"')
            if len(user_texts) > 1:
                goals.append("Follow up naturally, mirroring the caller path from the source run")
        else:
            goals.append("Revisit the situation observed in the source run")
        goals.append("End the call politely")

    if not constraints:
        constraints = ["Stay natural and spoken; never mention being a simulation or a test"]

    # Brief: short mission statement only — transcript sample goes to Context.notes.
    brief_bits = [
        f"Promoted from run `{source_run_id}` (source scenario `{source_scenario}`).",
        "Replay a similar caller path; pursue the listed goals, stay natural and spoken.",
    ]
    if src_persona.get("brief"):
        brief_bits.append(f"Original brief (reference): {_redact(str(src_persona['brief'])[:300])}")
    brief = " ".join(brief_bits)

    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    barge_count = int(metrics.get("barge_count") or 0)
    behavior = (summary.get("caller") or {}).get("behavior_summary") or {}
    if not barge_count and isinstance(behavior, dict):
        barge_count = int(behavior.get("barges_fired") or 0)

    warnings: list[str] = [
        "DRAFT — review Persona goals/constraints, Behavior, and Assert before promoting to CI.",
        "PII redaction is best-effort (email/phone/card patterns only).",
    ]

    behavior_spec = _behavior_from_events(events)
    has_barge_stub = bool(behavior_spec and behavior_spec.get("barge_ins"))
    script_open = _script_open_for_user_first(
        first_speaker=first_speaker,
        user_texts=user_texts,
        scenario_file=scenario_file_s,
    )
    if script_open:
        warnings.append(
            "Script open added for first_speaker=user (avoids dead-air when Behavior "
            "barge suppresses Gemini bootstrap). Review the open line before CI."
        )

    outcomes: list[dict[str, Any]] = []
    if agent_texts:
        # weak but useful: agent produced speech
        outcomes.append(
            {
                "id": "agent_spoke",
                "type": "transcript_contains",
                "role": "agent",
                "phrases": ["a", "e", "i", "o", "u"],
            }
        )
    if barge_count > 0 or has_barge_stub:
        outcomes.append(
            {
                "id": "recovered_after_barge",
                "type": "recovery",
                "min_agent_finals_after_barge_in": 1,
                "min_interruptions": 0,
            }
        )
        if behavior_spec:
            warnings.append(
                f"Source run had barge_count={barge_count}; Behavior stub + recovery Assert "
                "reconstructed from run markers — review timing (after_agent_ms) before CI."
            )
        else:
            warnings.append(
                f"Source run had barge_count={barge_count} but no sim.script.cue markers to "
                "reconstruct; recovery Assert added — re-add Script/Behavior barge cues manually."
            )
    elif behavior_spec:
        warnings.append(
            "Behavior noise stub reconstructed from run markers — review before CI."
        )

    # optional latency comment values from metrics (not auto-assert — too tight for cold starts)
    tt = metrics.get("turn_taking_ms") if isinstance(metrics.get("turn_taking_ms"), dict) else {}
    ttfw = metrics.get("ttfw_ms")
    latency_hint = None
    if tt.get("p95") is not None or ttfw is not None:
        latency_hint = {
            "observed_turn_p95_ms": tt.get("p95"),
            "observed_ttfw_ms": ttfw,
            "suggested_assert_example": {
                "id": "speed",
                "type": "latency",
                "max_turn_p95_ms": int(float(tt["p95"]) * 1.5) if tt.get("p95") is not None else 8000,
                "max_ttfw_ms": int(float(ttfw) * 1.5) if ttfw is not None else 15000,
                "require_turn_samples": 1,
            },
        }

    dispatch_md = _dispatch_from_source_scenario(scenario_file_s)
    if not dispatch_md:
        warnings.append(
            "Dispatch.metadata not recovered (source scenario file missing or had no Dispatch). "
            "Add Dispatch manually if the worker needs opaque metadata."
        )

    criteria = _pass_criteria_from_source(scenario_file_s)
    if not criteria:
        criteria = [
            "The agent responded to the caller",
            "The agent stayed on a helpful path for the caller's goals",
        ]
    verdict = summary.get("verdict") if isinstance(summary.get("verdict"), dict) else {}
    if str(verdict.get("verdict") or "").lower() == "fail" and verdict.get("notes"):
        note = _redact(str(verdict["notes"])[:240])
        criteria.append(f"Avoid the failure mode noted in source judge: {note}")

    status = summary.get("status")
    notes = (
        f"Promoted {datetime.now(timezone.utc).strftime('%Y-%m-%d')} from run `{source_run_id}` "
        f"(status={status}, turns={summary.get('turn_count')}, "
        f"judge={verdict.get('verdict') or 'n/a'}). "
        f"Observed metrics: ttfw_ms={ttfw}, turn_p95_ms={tt.get('p95')}, barge_count={barge_count}."
    )
    snippet = " | ".join(user_texts[:4])[:400]
    if snippet:
        notes += f" Caller transcript sample (reference only): {snippet}"
    if latency_hint:
        notes += (
            " Optional latency Assert (not auto-added): "
            + json.dumps(latency_hint["suggested_assert_example"], ensure_ascii=False)
        )

    lines: list[str] = [
        f"// DRAFT from run {source_run_id} — review before CI",
        "// Review checklist: 1) goals[] match the caller's real intent (not transcript echoes)",
        "// 2) Behavior barge/noise timing (after_agent_ms) fits your agent  3) tighten Assert",
        "// outcomes beyond the weak agent_spoke stub  4) confirm Dispatch metadata.",
        json.dumps(
            {
                "apiVersion": "agent-sim/v1",
                "kind": "Scenario",
                "metadata": {
                    "id": sid,
                    "locale": locale,
                    "tags": ["promoted", "from-run", source_scenario[:32]],
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        json.dumps(
            {
                "kind": "Persona",
                "spec": {
                    "name": name,
                    "language": language,
                    "brief": brief,
                    "goals": goals,
                    "style": str(src_persona.get("style") or "natural spoken language, concise"),
                    "traits": traits,
                    "constraints": constraints,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        json.dumps(
            {
                "kind": "Context",
                "spec": {
                    "notes": notes,
                    "fixtures": {
                        "source_run_id": source_run_id,
                        "source_scenario_id": source_scenario,
                    },
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        json.dumps(
            {
                "kind": "Execute",
                "spec": {
                    "max_turns": max_turns,
                    "timeout_s": timeout_s,
                    "first_speaker": first_speaker,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ]
    if dispatch_md:
        lines.append(
            json.dumps(
                {"kind": "Dispatch", "spec": {"metadata": dispatch_md}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if script_open:
        lines.append(
            json.dumps(
                {"kind": "Script", "spec": script_open},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if behavior_spec:
        lines.append(
            json.dumps(
                {"kind": "Behavior", "spec": behavior_spec},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if outcomes:
        lines.append(
            json.dumps(
                {
                    "kind": "Assert",
                    "spec": {"tools": [], "transcript": [], "outcomes": outcomes},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    lines.append(
        json.dumps(
            {"kind": "PassCriteria", "spec": {"criteria": criteria}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    kinds = []
    for line in lines:
        if line.startswith("//"):
            continue
        kinds.append(json.loads(line)["kind"])

    return {
        "scenario_id": sid,
        "source_run_id": source_run_id,
        "source_scenario_id": source_scenario,
        "jsonl": "\n".join(lines) + "\n",
        "kinds": kinds,
        "warnings": warnings,
        "notes": notes,
        "latency_hint": latency_hint,
        "behavior": behavior_spec,
        "script_open": script_open,
        "stats": {
            "user_finals": len(user_texts),
            "agent_finals": len(agent_texts),
            "barge_count": barge_count,
            "behavior_stub": bool(behavior_spec),
            "script_open": bool(script_open),
            "duration_ms": summary.get("duration_ms"),
            "status": status,
        },
    }
