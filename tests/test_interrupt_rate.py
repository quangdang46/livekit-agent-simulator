"""#25 interruption_rate — parse/validation + parallel InterruptRateRunner."""

from __future__ import annotations

import asyncio

import pytest

from livekit_agent_simulator.interrupt_rate import (
    InterruptRateRunner,
    InterruptRateSpec,
    parse_interrupt_rate,
)


def _persona(sc: dict) -> dict:
    return {"brief": "x", "speech_conditions": sc}


# ── parse / validation ────────────────────────────────────────────────


def test_parse_missing_or_none() -> None:
    assert parse_interrupt_rate(None) is None
    assert parse_interrupt_rate({}) is None
    assert parse_interrupt_rate(_persona({})) is None
    assert parse_interrupt_rate(_persona({"interruption_rate": "none"})) is None
    assert parse_interrupt_rate(_persona({"interruption_rate": "off"})) is None


def test_parse_interval_map() -> None:
    for rate, interval in (("low", 90_000), ("medium", 45_000), ("high", 30_000)):
        spec = parse_interrupt_rate(_persona({"interruption_rate": rate}))
        assert spec is not None
        assert spec.rate == rate
        assert spec.interval_ms == interval
        assert spec.interrupt_class == "correction"
        assert spec.delivery == "gemini_text"
        assert spec.with_blip is True


def test_parse_invalid_rate_raises() -> None:
    with pytest.raises(ValueError, match="interruption_rate"):
        parse_interrupt_rate(_persona({"interruption_rate": "extreme"}))


def test_parse_silent_mode_disables_but_still_validates() -> None:
    assert (
        parse_interrupt_rate(
            _persona({"interruption_rate": "high", "silent_mode": True})
        )
        is None
    )
    with pytest.raises(ValueError, match="interruption_rate"):
        parse_interrupt_rate(
            _persona({"interruption_rate": "bogus", "silent_mode": True})
        )


def test_parse_overrides() -> None:
    spec = parse_interrupt_rate(
        _persona(
            {
                "interruption_rate": "medium",
                "interruption_interval_ms": 12_000,
                "interruption_say": "Hold on —",
                "interruption_asset": "builtin:voice.barge_short",
                "interruption_class": "escalate",
                "interruption_gain": 0.5,
                "interruption_min_agent_active_ms": 300,
            }
        )
    )
    assert spec is not None
    assert spec.interval_ms == 12_000
    assert spec.say == "Hold on —"
    assert spec.asset == "builtin:voice.barge_short"
    assert spec.delivery == "room_pcm"
    assert spec.interrupt_class == "escalate"
    assert spec.gain == 0.5
    assert spec.with_blip is False  # voice.* WAV already carries energy
    assert spec.min_agent_active_ms == 300


def test_parse_interval_floor_and_gain_bounds() -> None:
    with pytest.raises(ValueError, match="interruption_interval_ms"):
        parse_interrupt_rate(
            _persona({"interruption_rate": "high", "interruption_interval_ms": 1000})
        )
    with pytest.raises(ValueError, match="interruption_gain"):
        parse_interrupt_rate(
            _persona({"interruption_rate": "high", "interruption_gain": 1.5})
        )


def test_apply_caller_behavior_validates_rate() -> None:
    from livekit_agent_simulator.behavior_compile import apply_caller_behavior

    with pytest.raises(ValueError, match="interruption_rate"):
        apply_caller_behavior(_persona({"interruption_rate": "bogus"}), None, [], None)
    # Valid rate does not add ScriptSteps — policy is a parallel runner.
    steps, _ = apply_caller_behavior(
        _persona({"interruption_rate": "medium"}), None, [], None
    )
    assert steps == []


# ── runner ────────────────────────────────────────────────────────────


class _Obs:
    def __init__(self) -> None:
        self.agent_has_spoken = False
        self.agent_is_active_speaker = False
        self.active_ms = 0

    def agent_active_duration_ms(self) -> int:
        return self.active_ms


class _Bridge:
    def __init__(self) -> None:
        self.cues: list[dict] = []

    async def inject_cue(
        self,
        text: str,
        *,
        label: str = "",
        delivery: str = "gemini_text",
        asset: str | None = None,
        scenario_dir=None,
        gain: float = 1.0,
        loop: bool = False,
    ) -> None:
        self.cues.append(
            {"text": text, "label": label, "delivery": delivery, "asset": asset, "gain": gain}
        )


class _Writer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, kind: str, spec=None, **kw) -> None:
        self.events.append({"kind": kind, "spec": spec or {}})


def _spec(**kw) -> InterruptRateSpec:
    base = dict(
        rate="high",
        interval_ms=200,
        say="wait —",
        asset=None,
        delivery="gemini_text",
        interrupt_class="correction",
        gain=1.0,
        with_blip=False,
        min_agent_active_ms=100,
    )
    base.update(kw)
    return InterruptRateSpec(**base)


async def _drive(runner: InterruptRateRunner, seconds: float) -> None:
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.sleep(seconds)
    finally:
        runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_runner_does_not_fire_before_agent_speaks() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    runner = InterruptRateRunner(_spec(), obs, bridge, writer, poll_s=0.01)
    await _drive(runner, 0.4)
    assert runner.fired == 0
    assert bridge.cues == []
    # Policy announcement still emitted once.
    assert [e["kind"] for e in writer.events] == ["sim.interrupt_rate"]


@pytest.mark.asyncio
async def test_runner_waits_for_active_speaker_even_when_interval_due() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.active_ms = 500
    runner = InterruptRateRunner(_spec(), obs, bridge, writer, poll_s=0.01)
    task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.4)  # interval (200 ms) due, but agent not active speaker
    assert runner.fired == 0
    obs.agent_is_active_speaker = True
    await asyncio.sleep(0.3)
    assert runner.fired >= 1
    runner.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    cue = next(e for e in writer.events if e["kind"] == "sim.script.cue")
    assert cue["spec"]["step_id"] == "rate-barge-1"
    assert cue["spec"]["barge_in"] is True
    assert cue["spec"]["class"] == "correction"
    assert cue["spec"]["during_agent_speech"] is True
    interruptions = [e for e in writer.events if e["kind"] == "interruption"]
    assert interruptions and interruptions[0]["spec"]["by"] == "sim"
    assert interruptions[0]["spec"]["false_positive"] is False
    assert bridge.cues and bridge.cues[0]["delivery"] == "gemini_text"


@pytest.mark.asyncio
async def test_runner_respects_min_agent_active_ms() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.agent_is_active_speaker = True
    obs.active_ms = 50  # below min_agent_active_ms=100
    runner = InterruptRateRunner(_spec(), obs, bridge, writer, poll_s=0.01)
    await _drive(runner, 0.4)
    assert runner.fired == 0


@pytest.mark.asyncio
async def test_runner_spaces_fires_by_interval() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.agent_is_active_speaker = True
    obs.active_ms = 1000
    runner = InterruptRateRunner(_spec(interval_ms=400), obs, bridge, writer, poll_s=0.01)
    task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.2)
    assert runner.fired == 0  # armed, first interval not yet elapsed
    await asyncio.sleep(0.4)
    assert runner.fired == 1
    await asyncio.sleep(0.45)
    assert runner.fired == 2
    runner.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    ids = [
        e["spec"]["step_id"] for e in writer.events if e["kind"] == "sim.script.cue"
    ]
    assert ids == ["rate-barge-1", "rate-barge-2"]


@pytest.mark.asyncio
async def test_runner_blip_before_text_barge() -> None:
    obs, bridge, writer = _Obs(), _Bridge(), _Writer()
    obs.agent_has_spoken = True
    obs.agent_is_active_speaker = True
    obs.active_ms = 1000
    runner = InterruptRateRunner(
        _spec(with_blip=True), obs, bridge, writer, poll_s=0.01
    )
    await _drive(runner, 0.5)
    assert runner.fired >= 1
    assert bridge.cues[0]["asset"] == "builtin:noise.blip"
    assert bridge.cues[0]["delivery"] == "room_pcm"
    assert bridge.cues[1]["delivery"] == "gemini_text"
    assert bridge.cues[1]["text"] == "wait —"
