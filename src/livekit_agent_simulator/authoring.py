"""Hamming-style authoring quality checks for scenarios (P1.G).

Warnings only — never flip ``valid`` false for soft quality issues.
Callers (validate_scenario) append these to the existing warnings list.
"""

from __future__ import annotations

from typing import Any

from .script.models import counts_for_recovery_barge

# Traits that imply interaction stress (soft prompt alone is not enough for CI).
STRESS_TRAITS = frozenset(
    {
        "interrupts",
        "impatient",
        "hangup_threat",
        "angry",
        "urgent",
        "backchannel",
        "silent",
        "quiet",
    }
)


def _persona_goals(persona: dict[str, Any]) -> list[str]:
    goals = persona.get("goals") or []
    if isinstance(goals, str):
        goals = [goals]
    return [str(g).strip() for g in goals if str(g).strip()]


def _persona_traits(persona: dict[str, Any]) -> list[str]:
    traits = persona.get("traits") or persona.get("behaviors") or []
    if isinstance(traits, str):
        traits = [traits]
    out = []
    for t in traits:
        key = str(t).strip().lower().replace(" ", "_").replace("-", "_")
        if key:
            out.append(key)
    return out


def _has_recovery_proof(scenario: Any) -> bool:
    """True if Assert recovery outcome or script_verify recovery min is set."""
    asserts = getattr(scenario, "asserts", None)
    if asserts is not None:
        for oc in getattr(asserts, "outcomes", None) or []:
            if getattr(oc, "type", None) == "recovery":
                return True
    sv = getattr(scenario, "script_verify", None)
    if sv is not None and int(getattr(sv, "min_agent_finals_after_barge_in", 0) or 0) > 0:
        return True
    return False


def _recovery_barge_steps(scenario: Any) -> list[Any]:
    steps = list(getattr(scenario, "script_steps", None) or [])
    return [
        s
        for s in steps
        if counts_for_recovery_barge(
            barge_in=bool(getattr(s, "barge_in", False)),
            interrupt_class=getattr(s, "interrupt_class", None),
        )
    ]


def collect_authoring_warnings(scenario: Any) -> list[str]:
    """Return soft authoring warnings for a parsed Scenario."""
    warnings: list[str] = []
    persona = getattr(scenario, "persona", None) or {}
    if not isinstance(persona, dict):
        persona = {}

    goals = _persona_goals(persona)
    if not goals:
        warnings.append(
            "Persona.goals is empty — Hamming: caller needs a job-to-be-done "
            "(underspecified personas pass on different agent workflows)."
        )

    brief = str(persona.get("brief") or "").strip()
    if not brief:
        warnings.append("Persona.brief is empty — add who is calling and why.")

    tags = []
    # Scenario.tags may live on metadata; export uses s.tags if present
    raw_tags = getattr(scenario, "tags", None) or []
    if isinstance(raw_tags, (list, tuple)):
        tags = [str(t).lower() for t in raw_tags]
    riskish = {"blocking", "scheduled", "exploratory", "draft", "smoke", "regression"}
    if tags and not any(t in riskish or t.startswith("risk:") for t in tags):
        warnings.append(
            "Scenario tags have no risk/lifecycle hint "
            "(prefer one of: smoke, draft, blocking, scheduled, exploratory, regression)."
        )

    traits = _persona_traits(persona)
    stress = [t for t in traits if t in STRESS_TRAITS]
    steps = list(getattr(scenario, "script_steps", None) or [])
    has_interaction = bool(steps) or bool(getattr(scenario, "behavior_spec", None))
    if stress and not has_interaction:
        warnings.append(
            f"Traits {stress} imply interaction stress but there is no Script/Behavior/"
            f"speech_conditions step — CI cannot hard-prove interrupt/silence/hangup "
            f"(prompt-only traits are soft)."
        )

    barges = _recovery_barge_steps(scenario)
    if barges and not _has_recovery_proof(scenario):
        ids = ", ".join(getattr(s, "id", "?") for s in barges[:5])
        warnings.append(
            f"Recovery barge step(s) present ({ids}) but no Assert outcome type=recovery "
            f"and script_verify.min_agent_finals_after_barge_in is 0 — "
            f"add recovery assert so CI proves agent re-engages."
        )

    # hang_up without ended_by
    hangups = [s for s in steps if getattr(s, "action", None) == "hang_up"]
    if hangups:
        asserts = getattr(scenario, "asserts", None)
        has_ended = False
        if asserts is not None:
            for oc in getattr(asserts, "outcomes", None) or []:
                if getattr(oc, "type", None) == "ended_by":
                    has_ended = True
                    break
        if not has_ended:
            warnings.append(
                "Script hang_up present but no Assert outcome type=ended_by — "
                "add ended_by to prove which side ended the call."
            )

    # DTMF draft reminder
    dtmf_steps = [s for s in steps if getattr(s, "action", None) == "dtmf"]
    if dtmf_steps and "draft" not in tags:
        warnings.append(
            "Script action=dtmf present — tag scenario draft until the agent under test "
            "handles SIP DTMF (sim can send; many agents only parse spoken digits)."
        )

    return warnings


def authoring_scorecard(scenario: Any) -> dict[str, Any]:
    """Lightweight 0–2 style dimensions for report/debug (not a hard gate)."""
    persona = getattr(scenario, "persona", None) or {}
    if not isinstance(persona, dict):
        persona = {}
    goals = _persona_goals(persona)
    constraints = persona.get("constraints") or []
    if isinstance(constraints, str):
        constraints = [constraints]
    constraints = [c for c in constraints if str(c).strip()]
    barges = _recovery_barge_steps(scenario)
    has_assert = getattr(scenario, "asserts", None) is not None
    dims = {
        "goals": 2 if goals else 0,
        "constraints": 2 if constraints else (1 if goals else 0),
        "behavior": 2 if barges or getattr(scenario, "behavior_spec", None) else (
            1 if getattr(scenario, "script_steps", None) else 0
        ),
        "assertion": 2 if has_assert and _has_recovery_proof(scenario) else (
            1 if has_assert else 0
        ),
    }
    total = sum(dims.values())
    return {"dimensions": dims, "total": total, "max": 8}
