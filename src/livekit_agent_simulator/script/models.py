"""Script step / verify models (pure data, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_TRIGGERS = frozenset({"agent_speaking", "silence", "time"})
SUPPORTED_ACTIONS = frozenset({"speak", "wait", "hang_up"})

# Hamming-aligned mid-call input classes (P1.F). JSON field name: ``class``.
INTERRUPTION_CLASSES = frozenset(
    {"correction", "backchannel", "noise", "dtmf", "silence", "escalate"}
)
# Only these (with barge_in) feed recovery asserts / barge_recovery_rate.
RECOVERY_BARGE_CLASSES = frozenset({"correction", "escalate"})


def normalize_interrupt_class(
    raw: Any,
    *,
    barge_in: bool = False,
    default_when_barge: str = "correction",
) -> str | None:
    """Return a supported class or None.

    ``barge_in=True`` without class defaults to ``correction`` so existing scenarios
    keep recovery semantics.
    """
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return default_when_barge if barge_in else None
    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "true_correction": "correction",
        "correct": "correction",
        "barge": "correction",
        "ack": "backchannel",
        "uhhuh": "backchannel",
        "uh_huh": "backchannel",
        "false_positive": "noise",
        "false_interrupt": "noise",
        "click": "noise",
        "digit": "dtmf",
        "digits": "dtmf",
        "human": "escalate",
        "handoff": "escalate",
        "safety": "escalate",
    }
    key = aliases.get(key, key)
    if key not in INTERRUPTION_CLASSES:
        raise ValueError(
            f"unsupported interrupt class {raw!r} "
            f"(supported: {sorted(INTERRUPTION_CLASSES)})"
        )
    return key


def counts_for_recovery_barge(
    *,
    barge_in: bool,
    interrupt_class: str | None,
) -> bool:
    """True when this cue should drive recovery asserts / barge_recovery_rate."""
    if not barge_in:
        return False
    cls = interrupt_class or "correction"
    return cls in RECOVERY_BARGE_CLASSES


@dataclass(frozen=True)
class ScriptStep:
    id: str
    trigger: str
    delay_ms: int
    say: str = ""
    label: str = ""
    once: bool = True
    min_agent_active_ms: int = 400
    delivery: str = "gemini_text"  # gemini_text | room_pcm
    asset: str | None = None
    silence_after_cue_ms: int = 0
    action: str = "speak"

    # Continuous ambient bed for room_pcm noise (re-queues until hang-up).
    # Distinct from once= (fire this step once). Only valid with delivery=room_pcm.
    loop: bool = False  # speak | wait | hang_up
    # For silence trigger: only start counting idle after agent has spoken once.
    require_agent_spoke_first: bool = True
    # hang_up: do not fire while user spoke and agent has not answered that turn yet.
    require_agent_reply_this_turn: bool = True
    # hang_up: defer while last agent final still expects a caller reply (open ? / prompt).
    # After open_question_idle_ms of no user reply, hang_up may proceed (ghost hang).
    defer_on_open_question: bool = True
    open_question_idle_ms: int = 20000
    barge_in: bool = False
    # When barge_in + gemini_text: play builtin noise.blip first (audible cut-in).
    with_blip: bool = True
    # Linear playback gain for this cue (0.0–1.0). Applies to gemini_text TTS and room_pcm.
    gain: float = 1.0
    # Hamming class: correction | backchannel | noise | dtmf | silence | escalate
    interrupt_class: str | None = None
    # Overlay role: fixture (PCM/barge/noise) | line (forced say) | None → auto
    overlay: str | None = None


OVERLAY_ROLES = frozenset({"fixture", "line"})


def effective_overlay(step: ScriptStep) -> str:
    """Classify Script step as audio fixture vs forced spoken line."""
    if step.overlay in OVERLAY_ROLES:
        return str(step.overlay)
    if (
        step.barge_in
        or step.delivery == "room_pcm"
        or (step.interrupt_class or "") in ("noise", "backchannel", "dtmf", "silence")
    ):
        return "fixture"
    if step.action == "speak" and str(step.say or "").strip():
        return "line"
    return "fixture"


@dataclass(frozen=True)
class ScriptVerifySpec:
    require_during_agent_speech: bool = True
    min_agent_finals_after_first_cue: int = 0
    min_user_finals_after_first_cue: int = 0
    min_interruptions: int | None = None
    max_interruptions: int | None = None
    # After a silence-wait step, require agent transcript final later (agent re-prompts).
    min_agent_finals_after_silence: int = 0
    # After a barge_in cue, require agent to speak again (recovery).
    min_agent_finals_after_barge_in: int = 0
    plugins: tuple[str, ...] = ()
    plugin_options: dict[str, Any] = field(default_factory=dict)
