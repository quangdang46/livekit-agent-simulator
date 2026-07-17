"""Prompt section builders — Composite pieces of the Live system instruction.

Google Live best practice order: persona → conversational rules → guardrails.
Each section is a small Strategy; DefaultCallerPolicy composes them.

Modes:
- **Dialogue** (no Script): Persona situation/goals/outcome own speech.
- **Interaction / hybrid** (Script present): Script is an overlay (fixture or
  forced line); freestyle answers between cues are allowed.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .policy import CallerPolicyContext


@runtime_checkable
class PromptSection(Protocol):
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        """Return zero or more lines (no trailing join)."""
        ...


def _step_overlay(step: Any) -> str:
    """fixture | line — mirrors script.models.effective_overlay when available."""
    if isinstance(step, dict):
        raw = step.get("overlay")
        if raw in ("fixture", "line"):
            return str(raw)
        barge = bool(step.get("barge_in") or step.get("interrupt"))
        delivery = str(step.get("delivery") or "gemini_text")
        icls = str(step.get("class") or step.get("interrupt_class") or "").lower()
        action = str(step.get("action") or "speak")
        say = str(step.get("say") or step.get("text") or "").strip()
    else:
        raw = getattr(step, "overlay", None)
        if raw in ("fixture", "line"):
            return str(raw)
        barge = bool(getattr(step, "barge_in", False))
        delivery = str(getattr(step, "delivery", "gemini_text") or "gemini_text")
        icls = str(getattr(step, "interrupt_class", None) or "").lower()
        action = str(getattr(step, "action", "speak") or "speak")
        say = str(getattr(step, "say", "") or "").strip()
    if barge or delivery == "room_pcm" or icls in ("noise", "backchannel", "dtmf", "silence"):
        return "fixture"
    if action == "speak" and say:
        return "line"
    return "fixture"


class RoleSection:
    """Persona block (Google SI step 1)."""

    def render(self, ctx: CallerPolicyContext) -> list[str]:
        lang = ctx.locale
        lines = [
            "## PERSONA",
            "You are role-playing a HUMAN CALLER on a phone call with a voice assistant.",
            "You are NOT an assistant, agent, or support worker. Never offer help; you are the customer.",
            f"RESPOND IN {lang}. YOU MUST RESPOND UNMISTAKABLY IN {lang}.",
            "Keep every utterance short and natural like real phone speech (1-2 sentences).",
            "Never mention that you are an AI, a simulation, a test, or a judge.",
        ]
        p = ctx.persona
        if p.get("name"):
            lines.append(f"Your name: {p['name']}.")
        situation = p.get("situation") or p.get("brief")
        if situation:
            label = "Your situation" if p.get("situation") else "Who you are and why you are calling"
            lines.append(f"{label}: {situation}")
        if p.get("situation") and p.get("brief") and p.get("brief") != p.get("situation"):
            lines.append(f"Additional brief: {p['brief']}")
        outcome = p.get("outcome") or p.get("desired_outcome")
        if outcome:
            lines.append(
                f"Desired call outcome (what “done” looks like for you): {outcome}"
            )
        return lines


class GoalsSection:
    """Ordered goals = conversational rules (Google SI step 2)."""

    def render(self, ctx: CallerPolicyContext) -> list[str]:
        goals = ctx.goals()
        if not goals:
            return []
        lines = [
            "",
            "## CONVERSATIONAL RULES — YOUR GOALS",
            "Complete each goal before moving to the next. Treat this as a checklist.",
        ]
        for i, g in enumerate(goals, 1):
            lines.append(f"GOAL {i}: {g}")
        if ctx.script_steps:
            lines.extend(
                [
                    "",
                    "Rules when a Script overlay is present (hybrid / interaction):",
                    "1. You still pursue goals through natural answers when the assistant asks.",
                    "2. Forced Script lines are injected as SIMULATOR CUE — speak that line once.",
                    "3. Audio fixtures (barge WAV, noise, backchannel) are simulator-owned — do not invent barges.",
                    "4. Do NOT freestyle goodbye / [END_CALL]; Script hang-up ends the call.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Rules for goals (dialogue mode — you own speech):",
                    "1. Work through ALL goals one by one in a natural phone conversation.",
                    "2. Do NOT skip ahead to a later goal before the current one is addressed.",
                    "3. One-time steps (greet / identify / ask fee) then conversational loops (clarify, push back) are OK.",
                    "4. Do NOT say goodbye or [END_CALL] until ALL goals are addressed (or unmistakably impossible).",
                    "5. If the assistant cannot help with one goal, state that briefly and move to the next.",
                    "6. If the assistant goes off-topic, steer back to the current GOAL.",
                    "7. Do not people-please: follow HARD CONSTRAINTS even if that slows the call.",
                ]
            )
        return lines


class StyleTraitsSection:
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        lines: list[str] = []
        p = ctx.persona
        if p.get("style"):
            lines.append(f"Speaking style: {p['style']}")
        traits = ctx.traits()
        if traits:
            from ..persona_traits import expand_traits

            lines.append(
                "Caller behavior traits (follow while staying natural): "
                + ", ".join(traits)
            )
            lines.extend(expand_traits(traits))
        return lines


class ConstraintsSection:
    """Hard constraints with if→then examples (Google SI step 4)."""

    def render(self, ctx: CallerPolicyContext) -> list[str]:
        constraints = ctx.constraints()
        if not constraints:
            return []
        lines = [
            "",
            "## HARD CONSTRAINTS (do not violate)",
            "These override being helpful or agreeable. If a constraint conflicts with the assistant's request, follow the constraint.",
        ]
        for c in constraints:
            lines.append(f"- {c}")
        lines.extend(
            [
                "",
                "Examples of correct behavior:",
                "- If the assistant asks for a payment card number and a constraint forbids it: refuse briefly and do not invent digits.",
                "- If the assistant asks you to restart a full menu and a constraint forbids it: refuse or ask for a supervisor; do not restart.",
                "- If you are tempted to agree just to finish the call: re-read HARD CONSTRAINTS first.",
            ]
        )
        return lines


class SpeechConditionsSection:
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        sc = ctx.speech_conditions()
        if not sc:
            return []
        bits: list[str] = []
        if sc.get("barge_policy"):
            bits.append(
                "a timed interruption may be injected by the simulator "
                f"(barge_policy={sc.get('barge_policy')})"
            )
        rate = str(sc.get("interruption_rate") or sc.get("interrupt_rate") or "").strip().lower()
        if rate and rate not in ("none", "off", "0", "false"):
            bits.append(
                "the simulator will periodically cut you in while the assistant is "
                f"talking (interruption_rate={rate}); those cut-ins are simulator-owned "
                "audio — do not invent extra barge-ins yourself"
            )
        if sc.get("silent_mode") is True or str(sc.get("silent_mode") or "").lower() in (
            "1", "true", "yes", "on", "silent",
        ):
            bits.append(
                "SILENT MODE: you produce NO speech. Stay completely mute for the whole call. "
                "Do not greet, answer, or freestyle. The simulator enforces silence."
            )
        elif sc.get("silence_ms") or sc.get("user_silence_ms"):
            bits.append(
                "you may be forced silent by the simulator "
                f"(silence_ms={sc.get('silence_ms') or sc.get('user_silence_ms') or 'n/a'})"
            )
        if sc.get("noise") or sc.get("ambient"):
            bits.append("there may be background noise on the line")
        vg = sc.get("voice_gain", sc.get("voice_volume", sc.get("volume")))
        try:
            if vg is not None and float(vg) < 1.0:
                bits.append(
                    f"your mic level may be quiet (voice_gain={float(vg):.2f}; "
                    "simulator scales your speech audio)"
                )
        except (TypeError, ValueError):
            pass
        if not bits:
            return []
        return ["Speech conditions (simulator-enforced where noted): " + "; ".join(bits) + "."]


class ContextSection:
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        """Caller world hints only — never inject author/harness ``notes`` into SI.

        ``Context.notes`` are for humans reading the JSONL / reports. Putting
        \"Dialogue mode — no Script\" into the model as \"Background context you
        know\" makes the caller act like a test harness instead of a person.
        """
        lines: list[str] = []
        # Prefer explicit caller-facing keys if present; ignore author notes.
        knows = ctx.context.get("caller_knows") or ctx.context.get("world")
        if isinstance(knows, str) and knows.strip():
            lines.append(f"Things you already know about this call: {knows.strip()}")
        elif isinstance(knows, list):
            bits = [str(x).strip() for x in knows if str(x).strip()]
            if bits:
                lines.append(
                    "Things you already know about this call: " + "; ".join(bits[:12])
                )
        fixtures = ctx.context.get("fixtures")
        if isinstance(fixtures, dict) and fixtures:
            # Opaque hints — core does not interpret business keys.
            pairs = ", ".join(f"{k}={v}" for k, v in list(fixtures.items())[:12])
            lines.append(
                "You may know these test fixture hints (use only if natural): " + pairs
            )
        return lines


class ScriptTimingSection:
    """Script is an interaction overlay — not the only mouth of the caller."""

    def render(self, ctx: CallerPolicyContext) -> list[str]:
        if not ctx.script_steps:
            return []
        n = len(ctx.script_steps)
        n_fix = sum(1 for s in ctx.script_steps if _step_overlay(s) == "fixture")
        n_line = sum(1 for s in ctx.script_steps if _step_overlay(s) == "line")
        return [
            "",
            "## SCRIPT OVERLAY (simulator-owned timing)",
            f"This call has {n} timed Script step(s) "
            f"({n_line} forced line(s), {n_fix} audio fixture(s)).",
            "Script is an OVERLAY on your persona dialogue — not a full script of the whole call.",
            "Forced lines: when you receive a SIMULATOR CUE, speak that line aloud once immediately.",
            "Fixtures (barge WAV, noise, soft barge, DTMF): injected as audio — do not invent them.",
            "Between Script cues: if the assistant asks a direct question, answer in 1–2 natural phone sentences.",
            "Do NOT freestyle barge-ins or goodbye / [END_CALL] while Script steps remain.",
            "Only the final Script hang-up step ends the call. Freestyle farewell will FAIL the test.",
        ]


class FirstSpeakerSection:
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        sc = ctx.speech_conditions()
        silent = sc.get("silent_mode") is True or str(sc.get("silent_mode") or "").lower() in (
            "1", "true", "yes", "on", "silent",
        )
        if silent:
            return [
                "Silent mode: produce zero speech for the entire call. "
                "Do not open, greet, or answer — stay mute.",
            ]
        if ctx.script_steps:
            return [
                "Opening speech: stay silent at connect until a SIMULATOR CUE "
                "(or fixture) plays. Do not greet early on your own.",
            ]
        if ctx.first_speaker == "agent":
            return [
                "Wait for the assistant to greet you first, then respond "
                "(unless a simulator cue tells you otherwise).",
            ]
        return [
            "You speak first: after the call connects, greet briefly and state why "
            "you are calling (one short turn). Do this from persona — no separate cue.",
        ]


class GuardrailsSection:
    def render(self, ctx: CallerPolicyContext) -> list[str]:
        n = len(ctx.goals())
        has_script = bool(ctx.script_steps)
        lines = [
            "",
            "## GUARDRAILS",
            "Your job is to pursue your goals as the caller. You are not solving the assistant's job.",
            (
                "A timed Script hang-up will end the call — do not freestyle an ending."
                if has_script
                else "Only end the call when ALL goals are done (or unmistakably impossible after you tried)."
            ),
            "If you say goodbye or [END_CALL] early, the automated test will FAIL.",
            (
                "If the assistant asks a direct question between Script cues, answer in 1–2 natural sentences; "
                "do not start a long freestyle monologue or goodbye."
                if has_script
                else "If the assistant says something irrelevant, steer back to your current goal."
            ),
        ]
        if has_script:
            lines.extend(
                [
                    "Script overlay active: do NOT freestyle a goodbye or barge outside Script cues.",
                    "Natural short answers to the assistant are OK; wait for the simulator hang-up cue to end the call.",
                ]
            )
        else:
            lines.extend(
                [
                    "When your desired outcome is met (or unmistakably impossible after you tried), "
                    "say ONE short goodbye in your language and stop speaking. "
                    "A clear bye/goodbye ends the call — do not linger in thank-you loops. "
                    "Optionally append [END_CALL] once for the harness (do not read brackets aloud).",
                ]
            )
        lines.extend(
            [
                'NEVER pronounce the English words "end call", "hang up", or "END CALL", '
                "and do not read brackets aloud — that leaks into the room recording. "
                "The marker is for the test harness transcript only.",
            ]
        )
        if n and not has_script:
            lines.append(
                f"You have {n} numbered goal(s). Ending before they are addressed is a failure."
            )
        return lines


def build_default_sections() -> list[PromptSection]:
    """Google Live order: persona → rules → guardrails (+ portable extras)."""
    return [
        RoleSection(),
        GoalsSection(),
        StyleTraitsSection(),
        ConstraintsSection(),
        SpeechConditionsSection(),
        ContextSection(),
        ScriptTimingSection(),
        FirstSpeakerSection(),
        GuardrailsSection(),
    ]
