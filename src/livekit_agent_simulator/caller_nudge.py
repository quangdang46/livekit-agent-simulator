"""Nudge the simulated caller to speak after the agent greets first."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gemini.live_session import GeminiCallerBridge
    from .livekit.observer import Observer
    from .logging.event_writer import EventWriter

# Language-neutral: persona / scenario locale already constrain speech language.
AGENT_GREETED_NUDGE = (
    "(The agent has finished greeting you. Respond now in the language of your persona.)"
)


async def nudge_caller_after_agent_greeting(
    observer: "Observer",
    bridge: "GeminiCallerBridge",
    writer: "EventWriter",
    *,
    first_speaker: str,
    debounce_s: float = 1.0,
    poll_s: float = 0.15,
    silent_mode: bool = False,
) -> None:
    """When first_speaker is agent, persona-only runs stall without a text bootstrap.

    Silent mode (Coval): never nudge — unresponsive caller must stay mute.
    """
    if first_speaker != "agent":
        return
    if silent_mode or getattr(bridge, "_silent_mode", False) is True:
        writer.emit(
            "sim.agent_greeted_nudge_skipped",
            spec={"reason": "silent_mode"},
            source="sim",
            include_dialogue=False,
        )
        return

    nudged = False
    while not bridge.end_call.is_set():
        if nudged:
            return
        if observer.agent_has_spoken and not observer.user_has_spoken:
            await asyncio.sleep(debounce_s)
            if bridge.end_call.is_set() or observer.user_has_spoken:
                return
            try:
                await bridge.inject_cue(
                    AGENT_GREETED_NUDGE,
                    label="agent_greeted_nudge",
                )
                writer.emit(
                    "sim.agent_greeted_nudge",
                    spec={"debounce_s": debounce_s},
                    source="sim",
                    include_dialogue=False,
                )
                nudged = True
                return
            except RuntimeError:
                await asyncio.sleep(poll_s)
                continue
        await asyncio.sleep(poll_s)
