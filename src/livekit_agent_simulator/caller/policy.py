"""Caller policy protocol — Strategy for dialog control (portable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MidcallCue:
    """A text inject into the Live session (not PCM).

    kind values (forensics): bootstrap | reground | custom
    """

    text: str
    kind: str = "custom"
    label: str = "midcall"


@dataclass
class CallerPolicyContext:
    """Immutable-enough bag for policy builders (no I/O)."""

    persona: dict[str, Any]
    locale: str
    context: dict[str, Any] = field(default_factory=dict)
    script_steps: list[Any] = field(default_factory=list)
    first_speaker: str = "agent"

    def goals(self) -> list[str]:
        raw = self.persona.get("goals") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(g).strip() for g in raw if str(g).strip()]

    def constraints(self) -> list[str]:
        raw = self.persona.get("constraints") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(c).strip() for c in raw if str(c).strip()]

    def traits(self) -> list[str]:
        raw = self.persona.get("traits") or self.persona.get("behaviors") or []
        if isinstance(raw, str):
            raw = [raw]
        out: list[str] = []
        for t in raw:
            s = str(t).strip()
            if s:
                out.append(s)
        return out

    def speech_conditions(self) -> dict[str, Any]:
        sc = self.persona.get("speech_conditions") or self.persona.get("speechConditions") or {}
        return sc if isinstance(sc, dict) else {}


@runtime_checkable
class CallerPolicy(Protocol):
    """Strategy: how we steer Gemini-as-caller dialog (not Script timing)."""

    def build_system_instruction(self, ctx: CallerPolicyContext) -> str:
        """Full Live API system_instruction text."""
        ...

    def midcall_cues(self, ctx: CallerPolicyContext) -> list[MidcallCue]:
        """Optional text injects after connect / for re-ground hooks."""
        ...
