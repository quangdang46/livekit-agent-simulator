"""Caller dialog policy — Gemini system instruction + mid-call re-ground.

Design: Strategy (CallerPolicy) + Composite (PromptSection list).
Interaction timing stays in Script/Behavior; this package owns *dialog* text only.
"""

from __future__ import annotations

from .default_policy import DefaultCallerPolicy
from .policy import CallerPolicy, CallerPolicyContext, MidcallCue
from .prompt_sections import build_default_sections

__all__ = [
    "CallerPolicy",
    "CallerPolicyContext",
    "DefaultCallerPolicy",
    "MidcallCue",
    "build_default_sections",
    "build_persona_system_instruction",
]


def build_persona_system_instruction(
    *,
    persona: dict,
    locale: str,
    context: dict | None = None,
    script_steps: list | None = None,
    first_speaker: str = "agent",
    policy: CallerPolicy | None = None,
) -> str:
    """Facade used by Scenario — keeps call sites stable."""
    pol: CallerPolicy = policy or DefaultCallerPolicy()
    ctx = CallerPolicyContext(
        persona=dict(persona or {}),
        locale=str(locale or "en-US"),
        context=dict(context or {}),
        script_steps=list(script_steps or []),
        first_speaker=str(first_speaker or "agent"),
    )
    return pol.build_system_instruction(ctx)
