"""Recurring caller cut-ins — ``Persona.speech_conditions.interruption_rate`` (#25).

Coval-style interruption rate: ``none | low | medium | high`` map to a minimum
interval between simulator-injected barges. This runs as a **parallel policy**
next to the sequential :class:`ScriptRunner` — authored Script steps keep their
own arm order; rate barges fire only while the agent is the active speaker and
at most once per interval.

Not to be confused with:
- ``barge_policy`` — a single one-shot compiled barge step;
- ``silent_mode`` — mute caller (disables this policy);
- Behavior ``barge_ins[]`` — explicit authored cut-ins.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .behavior_compile import _is_voice_asset, silent_mode_enabled, speech_conditions_of
from .script import normalize_interrupt_class

if TYPE_CHECKING:
    from .gemini.live_session import GeminiCallerBridge
    from .livekit.observer import Observer
    from .logging.event_writer import EventWriter

# Minimum interval between simulator cut-ins per rate (Coval-ish spacing).
INTERRUPT_RATE_INTERVALS_MS: dict[str, int | None] = {
    "none": None,
    "low": 90_000,
    "medium": 45_000,
    "high": 30_000,
}

MIN_INTERVAL_MS = 5_000
DEFAULT_SAY = "Sorry — one second —"
DEFAULT_MIN_AGENT_ACTIVE_MS = 700


@dataclass(frozen=True)
class InterruptRateSpec:
    """Parsed + validated interruption-rate policy for one scenario."""

    rate: str
    interval_ms: int
    say: str
    asset: str | None
    delivery: str  # room_pcm when asset set, else gemini_text
    interrupt_class: str
    gain: float
    with_blip: bool
    min_agent_active_ms: int


def parse_interrupt_rate(persona: dict[str, Any] | None) -> InterruptRateSpec | None:
    """Parse ``speech_conditions.interruption_rate`` (+ ``interruption_*`` overrides).

    Returns ``None`` when the policy is off (missing / ``none`` / silent_mode).
    Raises ``ValueError`` on invalid values so scenario parse fails fast.
    """
    sc = speech_conditions_of(persona or {})
    raw = sc.get("interruption_rate", sc.get("interrupt_rate"))
    if raw is None:
        return None
    rate = str(raw).strip().lower()
    if rate in ("", "none", "off", "0", "false"):
        return None
    if rate not in INTERRUPT_RATE_INTERVALS_MS:
        raise ValueError(
            "Persona.speech_conditions.interruption_rate must be one of "
            "none|low|medium|high (got "
            f"{rate!r})"
        )
    interval_ms = INTERRUPT_RATE_INTERVALS_MS[rate]
    override = sc.get("interruption_interval_ms")
    if override is not None:
        interval_ms = int(override)
    if interval_ms is None or interval_ms < MIN_INTERVAL_MS:
        raise ValueError(
            "Persona.speech_conditions.interruption_interval_ms must be >= "
            f"{MIN_INTERVAL_MS} (got {interval_ms!r})"
        )
    gain = float(sc.get("interruption_gain", 1.0))
    if not 0.0 <= gain <= 1.0:
        raise ValueError(
            "Persona.speech_conditions.interruption_gain must be between 0.0 and 1.0"
        )
    try:
        interrupt_class = normalize_interrupt_class(
            sc.get("interruption_class") or "correction", barge_in=True
        )
    except ValueError as e:
        raise ValueError(f"Persona.speech_conditions.interruption_class: {e}") from e
    # Silent caller never speaks — rate policy is off like other auto barges.
    # (Validation above still runs so bad values fail even in silent scenarios.)
    if silent_mode_enabled(persona or {}):
        return None
    asset = sc.get("interruption_asset")
    asset_s = str(asset).strip() if asset else ""
    delivery = "room_pcm" if asset_s else "gemini_text"
    # Text barge: blip by default. Vocal WAV (voice.*) already carries energy — no blip.
    default_blip = not _is_voice_asset(asset_s) if asset_s else True
    with_blip = bool(sc.get("interruption_with_blip", default_blip))
    say = str(sc.get("interruption_say") or DEFAULT_SAY).strip()
    min_active = int(
        sc.get("interruption_min_agent_active_ms") or DEFAULT_MIN_AGENT_ACTIVE_MS
    )
    return InterruptRateSpec(
        rate=rate,
        interval_ms=int(interval_ms),
        say=say,
        asset=asset_s or None,
        delivery=delivery,
        interrupt_class=interrupt_class,
        gain=gain,
        with_blip=with_blip,
        min_agent_active_ms=max(100, min_active),
    )


class InterruptRateRunner:
    """Parallel timer policy: recurring barges while the agent is speaking.

    Arms once the agent has spoken; each fire re-arms the interval. Fires only
    when the agent is the active speaker for >= ``min_agent_active_ms``, so a
    due interval quietly waits for the next agent turn instead of cutting into
    silence. Emits the same ``sim.script.cue`` / ``interruption`` events as
    Script barges so verify/summary count them identically.
    """

    def __init__(
        self,
        spec: InterruptRateSpec,
        observer: "Observer",
        bridge: "GeminiCallerBridge",
        writer: "EventWriter",
        *,
        scenario_dir: Path | None = None,
        poll_s: float = 0.05,
    ) -> None:
        self.spec = spec
        self.observer = observer
        self.bridge = bridge
        self.writer = writer
        self.scenario_dir = scenario_dir
        self.fired = 0
        self._poll_s = poll_s
        self._stop = asyncio.Event()
        self._armed_mono: float | None = None
        self._last_fire_mono: float | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        interval_s = self.spec.interval_ms / 1000.0
        self.writer.emit(
            "sim.interrupt_rate",
            spec={
                "rate": self.spec.rate,
                "interval_ms": self.spec.interval_ms,
                "class": self.spec.interrupt_class,
                "say": self.spec.say,
                "asset": self.spec.asset,
                "delivery": self.spec.delivery,
                "min_agent_active_ms": self.spec.min_agent_active_ms,
            },
            source="sim.interrupt_rate",
            include_dialogue=False,
        )
        while not self._stop.is_set():
            await asyncio.sleep(self._poll_s)
            if not self.observer.agent_has_spoken:
                continue
            if self._armed_mono is None:
                self._armed_mono = time.monotonic()
            anchor = self._last_fire_mono or self._armed_mono
            if (time.monotonic() - anchor) * 1000 < self.spec.interval_ms:
                continue
            if not self.observer.agent_is_active_speaker:
                continue
            active_ms = self.observer.agent_active_duration_ms() or 0
            if active_ms < self.spec.min_agent_active_ms:
                continue
            waited_ms = int((time.monotonic() - anchor) * 1000)
            await self._fire(waited_ms)

    async def _fire(self, waited_ms: int) -> None:
        self.fired += 1
        self._last_fire_mono = time.monotonic()
        step_id = f"rate-barge-{self.fired}"
        inject_error: str | None = None
        agent_active_ms = self.observer.agent_active_duration_ms() or 0
        during_agent_speech = bool(self.observer.agent_is_active_speaker)

        if self.spec.with_blip and self.spec.delivery != "room_pcm":
            try:
                await self.bridge.inject_cue(
                    "[barge blip]",
                    label=f"{step_id}-blip",
                    delivery="room_pcm",
                    asset="builtin:noise.blip",
                    scenario_dir=self.scenario_dir,
                )
            except Exception as blip_err:  # noqa: BLE001 — keep the policy alive
                self.writer.emit(
                    "sim.script.error",
                    spec={
                        "step_id": step_id,
                        "label": f"{step_id}-blip",
                        "delivery": "room_pcm",
                        "asset": "builtin:noise.blip",
                        "error": f"{type(blip_err).__name__}: {blip_err}",
                    },
                    source="sim.interrupt_rate",
                    include_dialogue=False,
                )
        try:
            await self.bridge.inject_cue(
                self.spec.say,
                label=step_id,
                delivery=self.spec.delivery,
                asset=self.spec.asset,
                scenario_dir=self.scenario_dir,
                gain=self.spec.gain,
                loop=False,
            )
        except Exception as e:  # noqa: BLE001 — keep the policy alive
            inject_error = f"{type(e).__name__}: {e}"
            self.writer.emit(
                "sim.script.error",
                spec={
                    "step_id": step_id,
                    "label": step_id,
                    "delivery": self.spec.delivery,
                    "asset": self.spec.asset,
                    "error": inject_error,
                },
                source="sim.interrupt_rate",
                include_dialogue=False,
            )
        if during_agent_speech and inject_error is None:
            icls = self.spec.interrupt_class
            self.writer.emit(
                "interruption",
                spec={
                    "by": "sim",
                    "barge_in": True,
                    "class": icls,
                    "false_positive": icls in ("noise", "backchannel"),
                    "step_id": step_id,
                    "label": step_id,
                    "say": self.spec.say,
                    "note": "Interrupt-rate barge while agent was active speaker",
                },
                source="sim.interrupt_rate",
                include_dialogue=False,
            )
        self.writer.emit(
            "sim.script.cue",
            spec={
                "step_id": step_id,
                "label": step_id,
                "say": self.spec.say,
                "trigger": "agent_speaking",
                "action": "speak",
                "barge_in": True,
                "class": self.spec.interrupt_class,
                "overlay": "fixture",
                "delivery": self.spec.delivery,
                "asset": self.spec.asset,
                "gain": self.spec.gain,
                "loop": False,
                "waited_ms": waited_ms,
                "hold_silence_ms": 0,
                "agent_active": self.observer.agent_is_active_speaker,
                "agent_active_ms": agent_active_ms,
                "during_agent_speech": during_agent_speech,
                "rate": self.spec.rate,
                "interval_ms": self.spec.interval_ms,
                "fired": self.fired,
                "error": inject_error,
            },
            source="sim.interrupt_rate",
            include_dialogue=False,
        )
