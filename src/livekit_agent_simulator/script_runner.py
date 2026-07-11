"""Timed caller cues — inject speech / wait while exercising agent turn-taking."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gemini.live_session import GeminiCallerBridge
    from .livekit.observer import Observer
    from .logging.event_writer import EventWriter

# agent_speaking: fire while agent is active speaker (barge-in / backchannel)
# silence: fire after agent has been idle for delay_ms (optional require agent spoke first)
# time: fire delay_ms after step is armed (absolute wait from step order)
SUPPORTED_TRIGGERS = frozenset({"agent_speaking", "silence", "time"})
SUPPORTED_ACTIONS = frozenset({"speak", "wait"})


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
    action: str = "speak"  # speak | wait
    # For silence trigger: only start counting idle after agent has spoken once.
    require_agent_spoke_first: bool = True
    barge_in: bool = False
    # When barge_in + gemini_text: play builtin noise.blip first (audible cut-in).
    with_blip: bool = True


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

    def stop(self) -> None:
        self._stop.set()

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
                await self._fire(step, elapsed_ms)
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
        try:
            agent_active_ms = self.observer.agent_active_duration_ms() or 0
            # Strict: "cut across" only if agent is the active speaker *now*.
            # (Historical active_ms alone is not a live interrupt.)
            during_agent_speech = bool(self.observer.agent_is_active_speaker)

            inject_error: str | None = None
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
                    self.writer.emit(
                        "interruption",
                        spec={
                            "by": "sim",
                            "barge_in": True,
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
                    "delivery": step.delivery if step.action != "wait" else None,
                    "asset": step.asset if step.action != "wait" else None,
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
                self._armed_step_index += 1
                if self._armed_step_index < len(self.steps):
                    self._await_post_cue_gap = step.trigger == "agent_speaking"
                    self._post_cue_gap_since = None
                self._trigger_since.clear()
                self._trigger_gap_since.clear()


def build_caller_behavior_summary(events: list[dict]) -> dict[str, Any]:
    """Aggregate barge / silence / recovery stats for summary.json + report player.

    Always safe to call; zeros when the run had no scripted caller behavior.
    """
    cues = [e for e in events if e.get("kind") == "sim.script.cue"]
    waits = [e for e in events if e.get("kind") == "sim.script.wait"]
    barges = [e for e in cues if (e.get("spec") or {}).get("barge_in")]
    barges_during = [
        e for e in barges if (e.get("spec") or {}).get("during_agent_speech")
    ]
    cues_during = [
        e for e in cues if (e.get("spec") or {}).get("during_agent_speech")
    ]
    silence_events = [e for e in events if e.get("kind") == "silence.detected"]
    interruptions = [e for e in events if e.get("kind") == "interruption"]
    agent_finals = [e for e in events if e.get("kind") == "transcript.agent.final"]

    barge_ms = barges[0].get("ts_mono_ms") if barges else None
    silence_ms = waits[0].get("ts_mono_ms") if waits else None

    agent_after_barge = 0
    recovery_ms: int | None = None
    if barge_ms is not None:
        after = [
            int(e.get("ts_mono_ms") or 0)
            for e in agent_finals
            if int(e.get("ts_mono_ms") or 0) > int(barge_ms)
        ]
        agent_after_barge = len(after)
        if after:
            recovery_ms = after[0] - int(barge_ms)

    agent_after_silence = 0
    if silence_ms is not None:
        agent_after_silence = sum(
            1
            for e in agent_finals
            if int(e.get("ts_mono_ms") or 0) >= int(silence_ms)
        )

    assets: list[str] = []
    for e in events:
        if e.get("kind") not in ("sim.script.cue", "sim.script_inject"):
            continue
        a = (e.get("spec") or {}).get("asset")
        if a and str(a) not in assets:
            assets.append(str(a))

    return {
        "script_cues_fired": len(cues),
        "waits_fired": len(waits),
        "barges_fired": len(barges),
        "barges_during_agent": len(barges_during),
        "cues_during_agent": len(cues_during),
        "silences_held": len(waits),
        "silence_events": len(silence_events),
        "interruptions": len(interruptions),
        "agent_finals_after_barge": agent_after_barge,
        "agent_finals_after_silence": agent_after_silence,
        "recovery_ms": recovery_ms,
        "cue_assets": assets,
    }


def evaluate_script_log(
    events: list[dict],
    steps: list[ScriptStep],
    verify: ScriptVerifySpec | None = None,
    *,
    scenario: Any | None = None,
    project_root: Path | str | None = None,
) -> dict[str, object]:
    """Log-based PASS/FAIL for scripted adaptive scenarios (no LLM judge required)."""
    cues = [e for e in events if e.get("kind") in ("sim.script.cue", "sim.script.wait")]
    agent_finals = [e for e in events if e.get("kind") == "transcript.agent.final"]
    user_finals = [e for e in events if e.get("kind") == "transcript.user.final"]
    interruptions = [e for e in events if e.get("kind") == "interruption"]

    checks: list[dict[str, object]] = []

    for step in steps:
        matching = [c for c in cues if c.get("spec", {}).get("step_id") == step.id]
        if not matching:
            checks.append({"step_id": step.id, "pass": False, "reason": "script step not fired"})
            continue
        cue = matching[0]
        spec = cue.get("spec") or {}
        during = bool(spec.get("during_agent_speech"))
        if step.trigger == "agent_speaking" and step.action == "speak" and not during:
            checks.append(
                {
                    "step_id": step.id,
                    "pass": False,
                    "reason": "cue fired but agent was not active speaker",
                }
            )
            continue
        checks.append(
            {
                "step_id": step.id,
                "pass": True,
                "during_agent_speech": during,
                "trigger": step.trigger,
                "action": step.action,
            }
        )

    cue_ms = cues[0]["ts_mono_ms"] if cues else None
    silence_cues = [
        e
        for e in cues
        if (e.get("spec") or {}).get("trigger") == "silence"
        or (e.get("spec") or {}).get("action") == "wait"
    ]
    silence_ms = silence_cues[0]["ts_mono_ms"] if silence_cues else None
    barge_cues = [
        e
        for e in cues
        if e.get("kind") == "sim.script.cue"
        and (
            (e.get("spec") or {}).get("barge_in")
            or (
                (e.get("spec") or {}).get("during_agent_speech")
                and (e.get("spec") or {}).get("trigger") == "agent_speaking"
                and int((e.get("spec") or {}).get("waited_ms") or 9999) < 800
            )
        )
    ]
    barge_ms = barge_cues[0]["ts_mono_ms"] if barge_cues else None

    agent_after_cue = (
        sum(1 for e in agent_finals if cue_ms is not None and e.get("ts_mono_ms", 0) >= cue_ms)
        if cue_ms is not None
        else 0
    )
    user_after_cue = (
        sum(1 for e in user_finals if cue_ms is not None and e.get("ts_mono_ms", 0) >= cue_ms)
        if cue_ms is not None
        else 0
    )
    agent_after_silence = (
        sum(
            1
            for e in agent_finals
            if silence_ms is not None and e.get("ts_mono_ms", 0) >= silence_ms
        )
        if silence_ms is not None
        else 0
    )
    agent_after_barge = (
        sum(
            1
            for e in agent_finals
            if barge_ms is not None and e.get("ts_mono_ms", 0) > barge_ms
        )
        if barge_ms is not None
        else 0
    )

    verify = verify or ScriptVerifySpec()
    if verify.min_agent_finals_after_first_cue > 0:
        ok = agent_after_cue >= verify.min_agent_finals_after_first_cue
        checks.append(
            {
                "check": "min_agent_finals_after_first_cue",
                "pass": ok,
                "expected": verify.min_agent_finals_after_first_cue,
                "actual": agent_after_cue,
            }
        )
    if verify.min_user_finals_after_first_cue > 0:
        ok = user_after_cue >= verify.min_user_finals_after_first_cue
        checks.append(
            {
                "check": "min_user_finals_after_first_cue",
                "pass": ok,
                "expected": verify.min_user_finals_after_first_cue,
                "actual": user_after_cue,
            }
        )
    if verify.min_agent_finals_after_silence > 0:
        ok = agent_after_silence >= verify.min_agent_finals_after_silence
        checks.append(
            {
                "check": "min_agent_finals_after_silence",
                "pass": ok,
                "expected": verify.min_agent_finals_after_silence,
                "actual": agent_after_silence,
            }
        )
    if verify.min_agent_finals_after_barge_in > 0:
        ok = agent_after_barge >= verify.min_agent_finals_after_barge_in
        checks.append(
            {
                "check": "min_agent_finals_after_barge_in",
                "pass": ok,
                "expected": verify.min_agent_finals_after_barge_in,
                "actual": agent_after_barge,
            }
        )
    if verify.min_interruptions is not None:
        ok = len(interruptions) >= verify.min_interruptions
        checks.append(
            {
                "check": "min_interruptions",
                "pass": ok,
                "expected": verify.min_interruptions,
                "actual": len(interruptions),
            }
        )
    if verify.max_interruptions is not None:
        ok = len(interruptions) <= verify.max_interruptions
        checks.append(
            {
                "check": "max_interruptions",
                "pass": ok,
                "expected": verify.max_interruptions,
                "actual": len(interruptions),
            }
        )

    plugin_results: list[dict[str, object]] = []
    if verify.plugins:
        from .plugins.api import VerifyContext
        from .plugins.loader import ensure_plugins_loaded
        from .plugins.registry import get_verify

        if project_root is not None:
            ensure_plugins_loaded(
                project_root,
                list(scenario.plugin_modules) if scenario is not None else None,
            )
        for plugin_name in verify.plugins:
            fn = get_verify(plugin_name)
            if fn is None:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": f"verify plugin {plugin_name!r} is not registered",
                    }
                )
                continue
            if scenario is None or project_root is None:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": "plugin verify requires scenario and project_root",
                    }
                )
                continue
            opts = verify.plugin_options.get(plugin_name, {})
            if not isinstance(opts, dict):
                opts = {}
            ctx = VerifyContext(
                events=events,
                steps=steps,
                verify=verify,
                scenario=scenario,
                project_root=Path(project_root),
                plugin_name=plugin_name,
                options=dict(opts),
            )
            try:
                raw = fn(ctx)
            except Exception as e:
                checks.append(
                    {
                        "check": f"plugin:{plugin_name}",
                        "pass": False,
                        "reason": f"{type(e).__name__}: {e}",
                    }
                )
                continue
            passed = bool(raw.get("pass"))
            plugin_checks = raw.get("checks")
            if isinstance(plugin_checks, list):
                for item in plugin_checks:
                    if isinstance(item, dict):
                        checks.append({**item, "plugin": plugin_name})
            checks.append(
                {
                    "check": f"plugin:{plugin_name}",
                    "pass": passed,
                    "plugin": plugin_name,
                    "detail": raw.get("detail"),
                }
            )
            plugin_results.append({"plugin": plugin_name, "pass": passed, "result": raw})

    return {
        "script_steps": len(steps),
        "cues_fired": len([e for e in cues if e.get("kind") == "sim.script.cue"]),
        "waits_fired": len([e for e in cues if e.get("kind") == "sim.script.wait"]),
        "agent_finals_after_first_cue": agent_after_cue,
        "user_finals_after_first_cue": user_after_cue,
        "agent_finals_after_silence": agent_after_silence,
        "agent_finals_after_barge_in": agent_after_barge,
        "interruptions": len(interruptions),
        "checks": checks,
        "plugin_results": plugin_results,
        "pass": all(bool(c.get("pass")) for c in checks) if checks else False,
    }
