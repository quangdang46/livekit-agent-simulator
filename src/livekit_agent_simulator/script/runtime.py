"""In-call ScriptRunner — fires timed cues into the sim caller bridge."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .farewell import default_hangup_farewell
from .hang_up_gate import agent_left_open_turn
from .models import ScriptStep, effective_overlay

if TYPE_CHECKING:
    from ..gemini.live_session import GeminiCallerBridge
    from ..livekit.observer import Observer
    from ..logging.event_writer import EventWriter

class ScriptRunner:
    def __init__(
        self,
        steps: list[ScriptStep],
        observer: "Observer",
        bridge: "GeminiCallerBridge",
        writer: "EventWriter",
        *,
        scenario_dir: Path | None = None,
    ) -> None:
        self.steps = steps
        self.observer = observer
        self.bridge = bridge
        self.writer = writer
        self.scenario_dir = scenario_dir
        self._stop = asyncio.Event()
        self._fired: set[str] = set()
        self._firing: set[str] = set()
        self._trigger_since: dict[str, float] = {}
        self._trigger_gap_since: dict[str, float] = {}
        self._active_speaker_gap_tolerance_ms = 600
        self._armed_step_index = 0
        self._await_post_cue_gap = False
        self._post_cue_gap_since: float | None = None
        self._hang_up_defer_emitted: set[str] = set()
        # Wall-clock when hang_up first hit a defer reason (do not reset on new agent finals).
        self._hang_up_defer_since: dict[str, float] = {}

    def stop(self) -> None:
        self._stop.set()

    def has_pending_steps(self) -> bool:
        """True while at least one Script step has not finished firing yet."""
        if self._stop.is_set():
            return False
        return self._armed_step_index < len(self.steps)

    async def run(self) -> None:
        if not self.steps:
            return
        while not self._stop.is_set():
            for idx, step in enumerate(self.steps):
                if idx != self._armed_step_index:
                    continue
                if step.once and step.id in self._fired:
                    continue
                if step.id in self._firing:
                    continue
                if self._await_post_cue_gap:
                    if self.observer.agent_is_active_speaker:
                        self._post_cue_gap_since = None
                        continue
                    if self._post_cue_gap_since is None:
                        self._post_cue_gap_since = time.monotonic()
                        continue
                    gap_ms = int((time.monotonic() - self._post_cue_gap_since) * 1000)
                    if gap_ms < self._active_speaker_gap_tolerance_ms:
                        continue
                    self._await_post_cue_gap = False
                    self._post_cue_gap_since = None
                    self._trigger_since.pop(step.id, None)
                    self._trigger_gap_since.pop(step.id, None)
                if not self._trigger_active(step):
                    if step.id in self._trigger_since:
                        gap_start = self._trigger_gap_since.setdefault(step.id, time.monotonic())
                        gap_ms = int((time.monotonic() - gap_start) * 1000)
                        if gap_ms >= self._active_speaker_gap_tolerance_ms:
                            self._trigger_since.pop(step.id, None)
                            self._trigger_gap_since.pop(step.id, None)
                    continue
                self._trigger_gap_since.pop(step.id, None)
                started = self._trigger_since.setdefault(step.id, time.monotonic())
                elapsed_ms = int((time.monotonic() - started) * 1000)
                need = step.delay_ms
                if step.trigger == "agent_speaking":
                    need = step.min_agent_active_ms + step.delay_ms
                if elapsed_ms < need:
                    continue
                if step.action == "hang_up":
                    # Wrap-up: mute freestyle so Gemini cannot invent loops while Script bye awaits.
                    if hasattr(self.bridge, "suppress_persona_output"):
                        self.bridge.suppress_persona_output(1500)
                if not self._hang_up_ready(step):
                    # Hold the arm but do not accumulate delay while dialog is still open.
                    self._trigger_since.pop(step.id, None)
                    continue
                await self._fire(step, elapsed_ms)
            await asyncio.sleep(0.05)

    def _hang_up_ready(self, step: ScriptStep) -> bool:
        """True when hang_up is allowed; always True for speak/wait.

        Briefly defer while the agent still expects a reply, but use a **single wall-clock
        budget** from the first defer (`open_question_idle_ms`). New agent questions must
        not reset that budget (otherwise freestyle loops never hang up).
        """
        if step.action != "hang_up":
            return True
        reason: str | None = None
        if step.require_agent_reply_this_turn:
            if self.observer.user_has_spoken and not self.observer.agent_replied_this_turn:
                reason = "awaiting_agent_reply"
        if reason is None and step.defer_on_open_question:
            agent_text = self.observer.last_agent_final_text
            if agent_left_open_turn(agent_text):
                agent_t = self.observer.last_agent_final_mono
                user_t = self.observer.last_user_final_mono
                user_replied = (
                    user_t is not None and agent_t is not None and user_t > agent_t
                )
                if not user_replied:
                    reason = "open_agent_question"
        budget_ms = max(0, int(step.open_question_idle_ms))
        if reason is None:
            self._hang_up_defer_emitted.discard(step.id)
            self._hang_up_defer_since.pop(step.id, None)
            return True
        started = self._hang_up_defer_since.setdefault(step.id, time.monotonic())
        deferred_ms = int((time.monotonic() - started) * 1000)
        if deferred_ms >= budget_ms:
            # Budget exhausted — allow script goodbye even if agent just re-asked.
            self.writer.emit(
                "sim.script.hang_up_deferred",
                spec={
                    "step_id": step.id,
                    "reason": "defer_budget_exhausted",
                    "prior_reason": reason,
                    "deferred_ms": deferred_ms,
                    "budget_ms": budget_ms,
                    "last_agent_final": (self.observer.last_agent_final_text or "")[:240],
                },
                source="sim.script",
                include_dialogue=False,
            )
            return True
        if step.id not in self._hang_up_defer_emitted:
            self._hang_up_defer_emitted.add(step.id)
            self.writer.emit(
                "sim.script.hang_up_deferred",
                spec={
                    "step_id": step.id,
                    "reason": reason,
                    "deferred_ms": deferred_ms,
                    "budget_ms": budget_ms,
                    "last_agent_final": (self.observer.last_agent_final_text or "")[:240],
                },
                source="sim.script",
                include_dialogue=False,
            )
        return False

    async def _wait_agent_idle(self, *, timeout_s: float = 5.0) -> None:
        """Wait until agent stops speaking (or timeout) before Script farewell."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return
            if not bool(getattr(self.observer, "agent_is_active_speaker", False)):
                return
            await asyncio.sleep(0.05)

    def _trigger_active(self, step: ScriptStep) -> bool:
        if step.trigger == "agent_speaking":
            return self.observer.agent_is_active_speaker
        if step.trigger == "silence":
            if step.require_agent_spoke_first and not self.observer.agent_has_spoken:
                return False
            return not self.observer.agent_is_active_speaker
        if step.trigger == "time":
            return True
        return False

    async def _fire(self, step: ScriptStep, waited_ms: int) -> None:
        if step.once:
            self._firing.add(step.id)
        inject_error: str | None = None
        try:
            agent_active_ms = self.observer.agent_active_duration_ms() or 0
            # Strict: "cut across" only if agent is the active speaker *now*.
            # (Historical active_ms alone is not a live interrupt.)
            during_agent_speech = bool(self.observer.agent_is_active_speaker)

            hold_silence_ms = int(step.silence_after_cue_ms or 0)
            if step.action == "wait":
                kind = "sim.script.wait"
                # User long-silence: suppress persona TTS, pause dead_call, hold duration.
                if hold_silence_ms > 0:
                    if hasattr(self.bridge, "begin_scripted_user_silence"):
                        self.bridge.begin_scripted_user_silence(hold_silence_ms)
                    else:
                        self.bridge.suppress_persona_output(hold_silence_ms)
                    await asyncio.sleep(hold_silence_ms / 1000.0)
            elif step.action == "hang_up":
                kind = "sim.script.hang_up"
                # Human-caller fidelity: never silent-drop. Empty say → locale default goodbye.
                say_text = step.say.strip()
                if not say_text:
                    lang = None
                    cfg = getattr(self.bridge, "cfg", None)
                    sim = getattr(cfg, "simulator", None) if cfg is not None else None
                    lang = getattr(sim, "language", None) if sim is not None else None
                    if not lang:
                        voice = getattr(sim, "voice", None) if sim is not None else None
                        lang = getattr(voice, "language", None) if voice is not None else None
                    say_text = default_hangup_farewell(lang if isinstance(lang, str) else None)
                # Prefer a quiet gap before farewell so we do not talk over an agent
                # re-prompt (budget may have expired while agent was mid-sentence).
                await self._wait_agent_idle(timeout_s=5.0)
                if hasattr(self.bridge, "begin_script_hangup_farewell"):
                    self.bridge.begin_script_hangup_farewell()
                try:
                    try:
                        await self.bridge.inject_cue(
                            say_text,
                            label=step.label or step.id,
                            delivery=step.delivery or "gemini_text",
                            asset=step.asset,
                            scenario_dir=self.scenario_dir,
                            gain=step.gain,
                        )
                    except Exception as say_err:
                        inject_error = f"{type(say_err).__name__}: {say_err}"
                    # Let goodbye leave the room before disconnect (real human hang-up).
                    # Scale drain with utterance length so short farewells are not cut.
                    words = max(1, len(say_text.split()))
                    drain_s = min(10.0, max(5.0, 1.2 + words * 0.45))
                    if hasattr(self.bridge, "drain_persona_speech"):
                        await self.bridge.drain_persona_speech(timeout_s=drain_s)
                    else:
                        await asyncio.sleep(min(4.0, drain_s))
                    await asyncio.sleep(0.55)
                finally:
                    if hasattr(self.bridge, "end_script_hangup_farewell"):
                        self.bridge.end_script_hangup_farewell()
                # Mark caller disconnected by triggering end_call via bridge
                self.bridge.sim_hang_up()
                hang_spec = {
                    "step_id": step.id,
                    "label": step.label or step.id,
                    "say": say_text,
                    "trigger": step.trigger,
                    "action": step.action,
                    "barge_in": step.barge_in,
                    "class": step.interrupt_class,
                    "overlay": effective_overlay(step),
                    "delivery": step.delivery or "gemini_text",
                    "asset": step.asset,
                    "gain": step.gain,
                    "waited_ms": waited_ms,
                    "hold_silence_ms": 0,
                    "agent_active": self.observer.agent_is_active_speaker,
                    "agent_active_ms": agent_active_ms,
                    "during_agent_speech": during_agent_speech,
                    "error": inject_error,
                }
                # Script verify matches step_id on this kind (not only sim.script.cue).
                self.writer.emit(
                    kind,
                    spec=hang_spec,
                    source="sim.script",
                    include_dialogue=False,
                )
                self.writer.emit(
                    "sim.hang_up",
                    spec={
                        "step_id": step.id,
                        "label": step.label or step.id,
                        "say": say_text,
                        **({"error": inject_error} if inject_error else {}),
                    },
                    source="sim.script",
                    include_dialogue=False,
                )
                return
            else:
                kind = "sim.script.cue"
                try:
                    # Hard barge-in: always mix a short PCM blip into the mic first so
                    # the agent STT / stereo L channel actually "cuts across" speech.
                    # gemini_text alone is delayed TTS and rarely sounds like an interrupt.
                    if (
                        step.barge_in
                        and step.with_blip
                        and step.delivery != "room_pcm"
                    ):
                        try:
                            await self.bridge.inject_cue(
                                "[barge blip]",
                                label=f"{step.label or step.id}-blip",
                                delivery="room_pcm",
                                asset="builtin:noise.blip",
                                scenario_dir=self.scenario_dir,
                            )
                        except Exception as blip_err:  # noqa: BLE001
                            self.writer.emit(
                                "sim.script.error",
                                spec={
                                    "step_id": step.id,
                                    "label": f"{step.label or step.id}-blip",
                                    "delivery": "room_pcm",
                                    "asset": "builtin:noise.blip",
                                    "error": f"{type(blip_err).__name__}: {blip_err}",
                                },
                                source="sim.script",
                                include_dialogue=False,
                            )
                    await self.bridge.inject_cue(
                        step.say,
                        label=step.label or step.id,
                        delivery=step.delivery,
                        asset=step.asset,
                        scenario_dir=self.scenario_dir,
                        gain=step.gain,
                    )
                except Exception as e:  # noqa: BLE001 — keep script chain alive
                    inject_error = f"{type(e).__name__}: {e}"
                    self.writer.emit(
                        "sim.script.error",
                        spec={
                            "step_id": step.id,
                            "label": step.label or step.id,
                            "delivery": step.delivery,
                            "asset": step.asset,
                            "error": inject_error,
                        },
                        source="sim.script",
                        include_dialogue=False,
                    )
                if hold_silence_ms > 0 and inject_error is None:
                    self.bridge.suppress_persona_output(hold_silence_ms)
                # Forensic: mark sim-initiated cut-in so summary/web show interrupted turns.
                if step.barge_in and during_agent_speech and inject_error is None:
                    icls = step.interrupt_class or "correction"
                    self.writer.emit(
                        "interruption",
                        spec={
                            "by": "sim",
                            "barge_in": True,
                            "class": icls,
                            "false_positive": icls in ("noise", "backchannel"),
                            "step_id": step.id,
                            "label": step.label or step.id,
                            "say": step.say,
                            "note": "Script barge-in while agent was active speaker",
                        },
                        source="sim.script",
                        include_dialogue=False,
                    )
            self.writer.emit(
                kind,
                spec={
                    "step_id": step.id,
                    "label": step.label or step.id,
                    "say": step.say,
                    "trigger": step.trigger,
                    "action": step.action,
                    "barge_in": step.barge_in,
                    "class": step.interrupt_class,
                    "overlay": effective_overlay(step),
                    "delivery": step.delivery if step.action != "wait" else None,
                    "asset": step.asset if step.action != "wait" else None,
                    "gain": step.gain if step.action == "speak" else None,
                    "waited_ms": waited_ms,
                    "hold_silence_ms": hold_silence_ms if step.action == "wait" else 0,
                    "agent_active": self.observer.agent_is_active_speaker,
                    "agent_active_ms": agent_active_ms,
                    "during_agent_speech": during_agent_speech,
                    "error": inject_error,
                },
                source="sim.script",
                include_dialogue=False,
            )
        finally:
            self._firing.discard(step.id)
            if step.once:
                self._fired.add(step.id)
                # Silent/failed speak must not look like a successful cue chain — abort.
                if inject_error and step.action == "speak":
                    self._armed_step_index = len(self.steps)
                    self.bridge.sim_hang_up()
                else:
                    self._armed_step_index += 1
                    if self._armed_step_index < len(self.steps):
                        self._await_post_cue_gap = step.trigger == "agent_speaking"
                        self._post_cue_gap_since = None
                self._trigger_since.clear()
                self._trigger_gap_since.clear()


