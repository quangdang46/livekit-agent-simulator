"""DefaultCallerPolicy — Composite of PromptSections + mid-call bootstrap cues."""

from __future__ import annotations

from .policy import CallerPolicyContext, MidcallCue
from .prompt_sections import PromptSection, build_default_sections


class DefaultCallerPolicy:
    """Portable Gemini-as-caller policy (Strategy + Composite).

    Extensibility:
    - Pass custom ``sections`` to reorder/replace prompt blocks.
    - Subclass and override ``midcall_cues`` for re-ground injects.
    - Swap entire policy via Scenario/bridge injection later without touching Live I/O.
    """

    def __init__(self, sections: list[PromptSection] | None = None) -> None:
        self._sections = list(sections) if sections is not None else build_default_sections()

    def build_system_instruction(self, ctx: CallerPolicyContext) -> str:
        lines: list[str] = []
        for section in self._sections:
            part = section.render(ctx)
            if part:
                lines.extend(part)
        return "\n".join(lines)

    def midcall_cues(self, ctx: CallerPolicyContext) -> list[MidcallCue]:
        """Bootstrap / re-ground texts (Live send_realtime_input text).

        Interaction PCM/barge remains ScriptRunner — these are dialog steering only.
        """
        cues: list[MidcallCue] = []
        if ctx.first_speaker == "user":
            cues.append(
                MidcallCue(
                    text=(
                        "(The call just connected. You speak first per PERSONA: "
                        "greet briefly and state why you are calling in one short turn.)"
                    ),
                    kind="bootstrap",
                    label="first_speaker_user",
                )
            )
        goals = ctx.goals()
        if goals:
            # Soft re-ground available for future hooks (bridge may inject on timer/events).
            g0 = goals[0][:120]
            cues.append(
                MidcallCue(
                    text=(
                        f"(Stay on your caller goals. Current focus: GOAL 1 — {g0}. "
                        "Do not end the call early. Do not switch into assistant mode.)"
                    ),
                    kind="reground",
                    label="goal_reground",
                )
            )
        return cues
