"""#29 hold_music_timeout_s — parse/validation + conversation-loop hang-up."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from livekit_agent_simulator.run_orchestrator import _conversation_loop
from livekit_agent_simulator.scenario import ScenarioError, parse_scenario

BASE = """\
{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"hold-test","locale":"en-US"}}
{"kind":"Persona","spec":{"name":"Caller","brief":"Caller waiting on a stalled agent.","goals":["hear greeting"]}}
{"kind":"Simulator","spec":{"max_turns":6,"timeout_s":90,"first_speaker":"agent"}}
"""


def _write(tmp_path, extra: str):
    f = tmp_path / "hold-test.jsonl"
    f.write_text(BASE + extra, encoding="utf-8")
    return f


# ── parse / validation ────────────────────────────────────────────────


def test_parse_execute_hold_timeout(tmp_path) -> None:
    f = _write(tmp_path, '{"kind":"Execute","spec":{"hold_music_timeout_s":20}}\n')
    s = parse_scenario(f)
    assert s.execute is not None
    assert s.execute.hold_music_timeout_s == 20.0
    assert s.hold_music_timeout_s() == 20.0
    assert s.export_dict()["hold_music_timeout_s"] == 20.0


def test_parse_hold_timeout_default_off(tmp_path) -> None:
    f = _write(tmp_path, '{"kind":"Execute","spec":{"max_turns":4}}\n')
    s = parse_scenario(f)
    assert s.hold_music_timeout_s() is None
    assert s.export_dict()["hold_music_timeout_s"] is None


def test_parse_hold_timeout_range(tmp_path) -> None:
    for bad in (1, 0, 4.9, 301, -10):
        f = _write(
            tmp_path, f'{{"kind":"Execute","spec":{{"hold_music_timeout_s":{bad}}}}}\n'
        )
        with pytest.raises(ScenarioError, match="hold_music_timeout_s"):
            parse_scenario(f)


def test_parse_hold_timeout_not_a_number(tmp_path) -> None:
    f = _write(
        tmp_path, '{"kind":"Execute","spec":{"hold_music_timeout_s":"soon"}}\n'
    )
    with pytest.raises(ScenarioError, match="hold_music_timeout_s"):
        parse_scenario(f)


def test_parse_persona_alias(tmp_path) -> None:
    f = tmp_path / "alias.jsonl"
    f.write_text(
        '{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"alias","locale":"en-US"}}\n'
        '{"kind":"Persona","spec":{"name":"Caller","brief":"b","speech_conditions":{"hold_music_timeout_s":30}}}\n',
        encoding="utf-8",
    )
    s = parse_scenario(f)
    assert s.hold_music_timeout_s() == 30.0


def test_execute_wins_over_persona_alias(tmp_path) -> None:
    f = tmp_path / "both.jsonl"
    f.write_text(
        '{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"both","locale":"en-US"}}\n'
        '{"kind":"Persona","spec":{"name":"Caller","brief":"b","speech_conditions":{"hold_music_timeout_s":30}}}\n'
        '{"kind":"Execute","spec":{"hold_music_timeout_s":10}}\n',
        encoding="utf-8",
    )
    s = parse_scenario(f)
    assert s.hold_music_timeout_s() == 10.0


def test_persona_alias_invalid_fails_parse(tmp_path) -> None:
    f = tmp_path / "alias-bad.jsonl"
    f.write_text(
        '{"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"alias-bad","locale":"en-US"}}\n'
        '{"kind":"Persona","spec":{"name":"Caller","brief":"b","speech_conditions":{"hold_music_timeout_s":2}}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError, match="hold_music_timeout_s"):
        parse_scenario(f)


# ── conversation loop ─────────────────────────────────────────────────


class _Obs:
    def __init__(self) -> None:
        self.agent_disconnected = asyncio.Event()
        self.turn = 0
        self.agent_replied_this_turn = False
        self.agent_has_spoken = False
        self.last_agent_activity_mono = time.monotonic()


class _Bridge:
    def __init__(self) -> None:
        self.end_call = asyncio.Event()
        self.hang_ups = 0
        self.scripted = False

    def scripted_silence_active(self) -> bool:
        return self.scripted

    def sim_hang_up(self) -> None:
        self.hang_ups += 1
        self.end_call.set()


class _Writer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, kind: str, spec=None, **kw) -> None:
        self.events.append({"kind": kind, "spec": spec or {}})


def _scenario(hold: float | None) -> SimpleNamespace:
    return SimpleNamespace(hold_music_timeout_s=lambda: hold)


def _run(max_turns: int = 99, timeout_s: int = 10) -> SimpleNamespace:
    return SimpleNamespace(max_turns=max_turns, timeout_s=timeout_s, first_speaker="agent")


@pytest.mark.asyncio
async def test_hold_timeout_hangs_up_after_agent_dead_air() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.last_agent_activity_mono = time.monotonic() - 1.0
    reason = await _conversation_loop(
        _scenario(0.5), _run(), obs, bridge, writer, cfg_silence_s=60.0
    )
    assert reason == "hold_music_timeout"
    assert bridge.hang_ups == 1
    hold_events = [e for e in writer.events if e["kind"] == "sim.hold_timeout"]
    assert hold_events and hold_events[0]["spec"]["timeout_s"] == 0.5
    assert hold_events[0]["spec"]["agent_idle_ms"] >= 500


@pytest.mark.asyncio
async def test_hold_timeout_not_armed_before_agent_speaks() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = False
    obs.last_agent_activity_mono = time.monotonic() - 10.0
    # dead_call net (3 x 0.1 s) still owns the never-spoke case.
    reason = await _conversation_loop(
        _scenario(0.3), _run(), obs, bridge, writer, cfg_silence_s=0.1
    )
    assert reason == "dead_call_silence"
    assert bridge.hang_ups == 0


@pytest.mark.asyncio
async def test_hold_timeout_resets_on_agent_activity() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.last_agent_activity_mono = time.monotonic()

    async def keep_agent_alive() -> None:
        for _ in range(3):
            await asyncio.sleep(0.3)
            obs.last_agent_activity_mono = time.monotonic()

    started = time.monotonic()
    alive = asyncio.create_task(keep_agent_alive())
    reason = await _conversation_loop(
        _scenario(0.6), _run(), obs, bridge, writer, cfg_silence_s=60.0
    )
    await asyncio.gather(alive, return_exceptions=True)
    elapsed = time.monotonic() - started
    assert reason == "hold_music_timeout"
    # 3 resets at ~0.3 s spacing then a full 0.6 s idle window.
    assert elapsed >= 1.4


@pytest.mark.asyncio
async def test_hold_timeout_beats_dead_call_net_when_armed() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.last_agent_activity_mono = time.monotonic()
    # dead_call would fire at 3 x 0.1 = 0.3 s; hold timeout is longer (0.8 s)
    # and must stay authoritative while armed.
    reason = await _conversation_loop(
        _scenario(0.8), _run(), obs, bridge, writer, cfg_silence_s=0.1
    )
    assert reason == "hold_music_timeout"


@pytest.mark.asyncio
async def test_hold_timeout_ignores_scripted_user_silence() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.last_agent_activity_mono = time.monotonic() - 1.0
    bridge.scripted = True  # caller intentionally quiet — agent dead air still counts
    reason = await _conversation_loop(
        _scenario(0.5), _run(), obs, bridge, writer, cfg_silence_s=60.0
    )
    assert reason == "hold_music_timeout"


@pytest.mark.asyncio
async def test_no_hold_timeout_keeps_dead_call_behavior() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.last_agent_activity_mono = time.monotonic() - 10.0
    reason = await _conversation_loop(
        _scenario(None), _run(), obs, bridge, writer, cfg_silence_s=0.1
    )
    assert reason == "dead_call_silence"
    assert bridge.hang_ups == 0
