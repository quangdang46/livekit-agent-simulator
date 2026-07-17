"""Unit tests for scenario-from-run (P1.4 fail → golden)."""

import json
from pathlib import Path
from livekit_agent_simulator.scenario_from_run import build_scenario_draft_from_run


def _meta(run_id: str = "test-run-1234", scenario_id: str = "smoke-hello") -> dict:
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_file": None,
        "run_spec": {"max_turns": 4, "timeout_s": 120, "first_speaker": "user"},
        "dispatch_metadata_set": False,
        "agent_name": "voice-ai-worker-local",
    }


def _summary(
    *,
    turn_count: int = 3,
    duration_ms: int = 42000,
    status: str = "done",
    barge_count: int = 0,
    turn_p95: float | None = None,
    ttfw: int | None = None,
    verdict: str = "pass",
    verdict_notes: str = "",
) -> dict:
    m: dict = {
        "barge_count": barge_count,
        "barges_recovered": 0,
        "recovery_ms": {"count": 0, "p50": None, "p95": None},
        "turn_taking_ms": {"count": 1, "p50": 5000.0, "p95": turn_p95, "max": 8000.0, "min": 5000.0, "mean": 5000.0},
        "ttfw_ms": ttfw,
    }
    return {
        "run_id": "test-run-1234",
        "status": status,
        "duration_ms": duration_ms,
        "turn_count": turn_count,
        "metrics": m,
        "caller": {"behavior_summary": {"barges_fired": barge_count}},
        "verdict": {"verdict": verdict, "notes": verdict_notes},
    }


def _events(
    *,
    user_texts: list[str] | None = None,
    agent_texts: list[str] | None = None,
    extra: list[dict] | None = None,
) -> list[str]:
    lines: list[str] = []
    mono = 1000
    if user_texts:
        for t in user_texts:
            lines.append(json.dumps({"kind": "transcript.user.final", "ts_mono_ms": mono, "spec": {"text": t}}))
            mono += 5000
    if agent_texts:
        for t in agent_texts:
            lines.append(json.dumps({"kind": "transcript.agent.final", "ts_mono_ms": mono, "spec": {"text": t, "turn_taking_ms": 3000}}))
            mono += 5000
    for e in extra or []:
        lines.append(json.dumps({"ts_mono_ms": mono, **e}))
        mono += 1000
    if not lines:
        lines.append(json.dumps({"kind": "run.started", "ts_mono_ms": 0, "spec": {}}))
    return lines


def _barge_cue(*, cls: str = "correction", barge_in: bool = True, say: str = "Wait — actually the other one", asset: str | None = None, agent_active_ms: int = 2400, during: bool = True, error: str | None = None) -> dict:
    return {
        "kind": "sim.script.cue",
        "spec": {
            "step_id": "b1",
            "say": say,
            "trigger": "agent_speaking",
            "action": "speak",
            "barge_in": barge_in,
            "class": cls,
            "asset": asset,
            "agent_active_ms": agent_active_ms,
            "during_agent_speech": during,
            "error": error,
        },
    }


def _write_report(
    tmp_path: Path,
    run_id: str = "test-run-1234",
    meta: dict | None = None,
    summary: dict | None = None,
    events: list[str] | None = None,
) -> Path:
    report_dir = tmp_path / "reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    json.dump(meta or _meta(), (report_dir / "meta.json").open("w"))
    json.dump(summary or _summary(), (report_dir / "summary.json").open("w"))
    if events:
        (report_dir / "events.jsonl").write_text("\n".join(events), encoding="utf-8")
    return report_dir


def test_draft_from_basic_run(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        events=_events(user_texts=["Xin chào, tôi cần hỗ trợ", "Cảm ơn bạn"], agent_texts=["Chào bạn, tôi có thể giúp gì?"]),
    )
    draft = build_scenario_draft_from_run(report_dir)
    assert draft["scenario_id"].startswith("from-")
    assert draft["source_run_id"] == "test-run-1234"
    assert draft["kinds"] == ["Scenario", "Persona", "Context", "Execute", "Script", "Assert", "PassCriteria"]
    assert draft["stats"]["script_open"] is True
    assert "Xin chào" in draft["jsonl"]
    assert draft["stats"]["user_finals"] == 2
    assert draft["stats"]["agent_finals"] == 1

    # validate round-trip
    import json
    for line in draft["jsonl"].splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        obj = json.loads(s)
        assert "kind" in obj


def test_draft_barge_includes_recovery_assert(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        summary=_summary(barge_count=2, turn_p95=7000.0, ttfw=9000),
        events=_events(
            user_texts=["hello", "ok thanks"],
            agent_texts=["hi", "bye"],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir)
    assert "recovered_after_barge" in draft["jsonl"]
    assert draft["latency_hint"] is not None
    assert draft["latency_hint"]["observed_turn_p95_ms"] == 7000.0


def test_draft_with_scenario_file(tmp_path: Path) -> None:
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    scen_file = scen_dir / "my-source.jsonl"
    scen_file.write_text(
        json.dumps({"apiVersion": "agent-sim/v1", "kind": "Scenario", "metadata": {"id": "my-source", "locale": "vi-VN"}})
        + "\n"
        + json.dumps({"kind": "Persona", "spec": {"name": "Lan", "traits": ["polite", "chatty"], "brief": "Test", "goals": [], "style": "natural", "constraints": []}})
        + "\n"
        + json.dumps({"kind": "Dispatch", "spec": {"metadata": '{"customAgentId":"agent_xxx"}'}})
        + "\n",
        encoding="utf-8",
    )
    report_dir = _write_report(
        tmp_path,
        meta=_meta(scenario_id="my-source") | {"scenario_file": str(scen_file)},
        events=_events(user_texts=["hi"], agent_texts=["hello"]),
    )
    draft = build_scenario_draft_from_run(report_dir, scenario_id="my-promoted-v1")
    assert draft["scenario_id"] == "my-promoted-v1"
    assert "agent_xxx" in draft["jsonl"] or '"customAgentId"' in draft["jsonl"]
    loc = draft["jsonl"].splitlines()
    # Dispatch metadata is re-serialized into JSON → inner quotes get escaped
    assert any('agent_xxx' in line for line in loc)


def test_draft_missing_report_raises(tmp_path: Path) -> None:
    try:
        build_scenario_draft_from_run(tmp_path / "nonexistent")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_brief_is_not_transcript_paste(tmp_path: Path) -> None:
    long_user = "My order number is 12345 and I want a refund because the item arrived broken"
    report_dir = _write_report(
        tmp_path,
        events=_events(user_texts=[long_user, "thanks"], agent_texts=["Sure, let me check"]),
    )
    draft = build_scenario_draft_from_run(report_dir)
    persona = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Persona"
    )
    assert long_user not in persona["spec"]["brief"]
    # Intent lands in goals, sample lands in Context.notes (author-only)
    assert any("Open with the same request" in g for g in persona["spec"]["goals"])
    ctx = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Context"
    )
    assert "transcript sample" in ctx["spec"]["notes"]
    assert persona["spec"]["constraints"], "derived constraints must not be empty"


def test_source_persona_goals_preferred(tmp_path: Path) -> None:
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    scen_file = scen_dir / "src.jsonl"
    scen_file.write_text(
        json.dumps({"apiVersion": "agent-sim/v1", "kind": "Scenario", "metadata": {"id": "src", "locale": "en-US"}})
        + "\n"
        + json.dumps({"kind": "Persona", "spec": {"name": "Sam", "brief": "b", "goals": ["Ask for a refund", "Confirm the refund timeline"], "constraints": ["Never share the card number"], "traits": [], "style": "s"}})
        + "\n",
        encoding="utf-8",
    )
    report_dir = _write_report(
        tmp_path,
        meta=_meta(scenario_id="src") | {"scenario_file": str(scen_file)},
        events=_events(user_texts=["hello there friend"], agent_texts=["hi"]),
    )
    draft = build_scenario_draft_from_run(report_dir)
    persona = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Persona"
    )
    assert persona["spec"]["goals"] == ["Ask for a refund", "Confirm the refund timeline"]
    assert persona["spec"]["constraints"] == ["Never share the card number"]


def test_barge_fail_run_gets_behavior_and_recovery_stub(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        summary=_summary(barge_count=1, verdict="fail", verdict_notes="agent stopped after barge"),
        events=_events(
            user_texts=["hello", "wait, actually"],
            agent_texts=["hi"],
            extra=[_barge_cue(say="Wait — actually the other one", agent_active_ms=2400)],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir)
    assert draft["stats"]["behavior_stub"] is True
    assert "Behavior" in draft["kinds"]
    assert "Script" in draft["kinds"]  # user-first open to avoid dead-air
    assert draft["stats"]["script_open"] is True
    behavior = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Behavior"
    )
    barge = behavior["spec"]["barge_ins"][0]
    assert barge["say"] == "Wait — actually the other one"
    assert barge["class"] == "correction"
    assert barge["after_agent_ms"] == 2400
    assert "recovered_after_barge" in draft["jsonl"]
    script = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Script"
    )
    assert script["spec"]["steps"][0]["id"] == "open"
    assert script["spec"]["steps"][0]["say"] == "hello"
    assert script["spec"]["steps"][0]["require_agent_spoke_first"] is False


def test_user_first_prefers_source_script_open(tmp_path: Path) -> None:
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    scen_file = scen_dir / "src.jsonl"
    scen_file.write_text(
        json.dumps({"apiVersion": "agent-sim/v1", "kind": "Scenario", "metadata": {"id": "src", "locale": "en-US"}})
        + "\n"
        + json.dumps({"kind": "Persona", "spec": {"name": "Sam", "brief": "b", "goals": ["g"], "constraints": [], "traits": [], "style": "s"}})
        + "\n"
        + json.dumps({"kind": "Script", "spec": {"steps": [{"id": "open", "trigger": "silence", "delay_ms": 2200, "say": "Hi - please run a full API lookup.", "once": True, "require_agent_spoke_first": False}]}})
        + "\n",
        encoding="utf-8",
    )
    report_dir = _write_report(
        tmp_path,
        meta=_meta(scenario_id="src") | {"scenario_file": str(scen_file)},
        events=_events(user_texts=["different transcript line"], agent_texts=["hi"]),
    )
    draft = build_scenario_draft_from_run(report_dir)
    script = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Script"
    )
    assert script["spec"]["steps"][0]["say"] == "Hi - please run a full API lookup."


def test_agent_first_skips_script_open(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        meta=_meta() | {"run_spec": {"max_turns": 4, "timeout_s": 120, "first_speaker": "agent"}},
        events=_events(user_texts=["hello"], agent_texts=["hi there"]),
    )
    draft = build_scenario_draft_from_run(report_dir)
    assert draft["stats"]["script_open"] is False
    assert "Script" not in draft["kinds"]


def test_noise_cue_becomes_false_interrupt(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        events=_events(
            user_texts=["hi"],
            agent_texts=["hello"],
            extra=[_barge_cue(cls="noise", barge_in=True, say="[noise]", asset="builtin:noise.loud")],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir)
    behavior = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Behavior"
    )
    fi = behavior["spec"]["false_interrupts"][0]
    assert fi["asset"] == "builtin:noise.loud"
    # noise alone must not add the recovery Assert
    assert "recovered_after_barge" not in draft["jsonl"]


def test_errored_cue_is_ignored(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        events=_events(
            user_texts=["hi"],
            agent_texts=["hello"],
            extra=[_barge_cue(error="InjectError: boom")],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir)
    assert draft["stats"]["behavior_stub"] is False
    assert "Behavior" not in draft["kinds"]


def test_interruption_marker_fallback(tmp_path: Path) -> None:
    report_dir = _write_report(
        tmp_path,
        events=_events(
            user_texts=["hi"],
            agent_texts=["hello"],
            extra=[{"kind": "interruption", "spec": {"by": "sim", "barge_in": True, "class": "question", "say": "Hang on, what?"}}],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir)
    behavior = next(
        json.loads(s) for s in draft["jsonl"].splitlines()
        if s.strip() and not s.startswith("//") and json.loads(s).get("kind") == "Behavior"
    )
    assert behavior["spec"]["barge_ins"][0]["say"] == "Hang on, what?"
    assert behavior["spec"]["barge_ins"][0]["class"] == "question"


def test_draft_round_trips_through_scenario_parser(tmp_path: Path) -> None:
    from livekit_agent_simulator.scenario import parse_scenario

    report_dir = _write_report(
        tmp_path,
        summary=_summary(barge_count=1),
        events=_events(
            user_texts=["hello, I need help with my order"],
            agent_texts=["hi, sure"],
            extra=[_barge_cue()],
        ),
    )
    draft = build_scenario_draft_from_run(report_dir, scenario_id="promoted-rt")
    out = tmp_path / "promoted-rt.jsonl"
    out.write_text(draft["jsonl"], encoding="utf-8")
    scenario = parse_scenario(out)
    assert scenario.id == "promoted-rt"
    # Behavior stub compiles into a barge ScriptStep; user-first open is present
    assert any(s.id == "open" for s in scenario.script_steps)
    assert any(getattr(s, "barge_in", False) for s in scenario.script_steps)
