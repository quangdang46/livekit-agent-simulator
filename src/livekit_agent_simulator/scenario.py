"""JSONL scenario loader + validator.

A scenario file is line-delimited JSON. Line 1 is the header; every following line is
a section keyed by `kind`:

    {"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"smoke-hello","locale":"ja-JP","tags":["smoke"]}}
    {"kind":"Persona","spec":{"name":"Tanaka","brief":"...","goals":["..."],"style":"..."}}
    {"kind":"Context","spec":{"notes":"..."}}
    {"kind":"Simulator","spec":{"max_turns":6,"timeout_s":120,"first_speaker":"agent"}}
    {"kind":"Execute","spec":{"max_turns":2,"timeout_s":90,"first_speaker":"user"}}
    {"kind":"Dispatch","spec":{"metadata":"{\"yourProjectKey\":\"value\"}"}}
    {"kind":"PassCriteria","spec":{"criteria":["agent greets the caller politely"]}}
    {"kind":"Script","spec":{"steps":[...],"verify":{...}}}
    {"kind":"Script","spec":{"steps":[{"id":"backchannel","trigger":"agent_speaking","delay_ms":800,"say":"うん","label":"backchannel-during-agent"}],"verify":{"require_during_agent_speech":true,"min_agent_finals_after_first_cue":1}}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .script_runner import ScriptStep, ScriptVerifySpec

API_VERSION = "agent-sim/v1"
KNOWN_KINDS = {"Persona", "Context", "Simulator", "Execute", "Dispatch", "PassCriteria", "Script"}


class ScenarioError(Exception):
    """Raised on malformed scenario files. Message includes file + line number."""


@dataclass
class SimulatorSpec:
    max_turns: int = 6
    timeout_s: int = 120
    first_speaker: str = "agent"  # agent | user


@dataclass
class ExecuteSpec:
    """Run parameters — when present, overrides Simulator for execution."""

    max_turns: int | None = None
    timeout_s: int | None = None
    first_speaker: str | None = None


@dataclass
class DispatchSpec:
    """Opaque LiveKit job metadata JSON — project-specific; MCP never interprets keys."""

    metadata: str | None = None


@dataclass
class Scenario:
    id: str
    path: Path
    locale: str = "ja-JP"
    tags: list[str] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    simulator: SimulatorSpec = field(default_factory=SimulatorSpec)
    execute: ExecuteSpec | None = None
    dispatch: DispatchSpec | None = None
    pass_criteria: list[str] = field(default_factory=list)
    script_steps: list[Any] = field(default_factory=list)
    script_verify: ScriptVerifySpec | None = None

    @property
    def run_spec(self) -> SimulatorSpec:
        """Effective run params: Execute overrides Simulator."""
        ex = self.execute
        if ex is None:
            return self.simulator
        return SimulatorSpec(
            max_turns=ex.max_turns if ex.max_turns is not None else self.simulator.max_turns,
            timeout_s=ex.timeout_s if ex.timeout_s is not None else self.simulator.timeout_s,
            first_speaker=ex.first_speaker if ex.first_speaker is not None else self.simulator.first_speaker,
        )

    def dispatch_metadata(self, config_default: str | None = None) -> str | None:
        """Scenario Dispatch.metadata wins over config livekit.dispatch_metadata."""
        if self.dispatch and self.dispatch.metadata:
            return self.dispatch.metadata
        return config_default

    def export_dict(self) -> dict[str, Any]:
        """Structured scenario for MCP export / agent inspection."""
        return {
            "id": self.id,
            "file": self.path.name,
            "locale": self.locale,
            "tags": self.tags,
            "persona": self.persona,
            "context": self.context,
            "simulator": {
                "max_turns": self.simulator.max_turns,
                "timeout_s": self.simulator.timeout_s,
                "first_speaker": self.simulator.first_speaker,
            },
            "execute": None
            if self.execute is None
            else {
                "max_turns": self.execute.max_turns,
                "timeout_s": self.execute.timeout_s,
                "first_speaker": self.execute.first_speaker,
            },
            "dispatch": None
            if self.dispatch is None
            else {"metadata_set": bool(self.dispatch.metadata)},
            "run": {
                "max_turns": self.run_spec.max_turns,
                "timeout_s": self.run_spec.timeout_s,
                "first_speaker": self.run_spec.first_speaker,
            },
            "pass_criteria": self.pass_criteria,
            "script_steps": len(self.script_steps),
            "script_verify": None
            if self.script_verify is None
            else {
                "require_during_agent_speech": self.script_verify.require_during_agent_speech,
                "min_agent_finals_after_first_cue": self.script_verify.min_agent_finals_after_first_cue,
                "min_user_finals_after_first_cue": self.script_verify.min_user_finals_after_first_cue,
                "min_interruptions": self.script_verify.min_interruptions,
                "max_interruptions": self.script_verify.max_interruptions,
            },
        }

    def persona_system_prompt(self) -> str:
        """Build the Gemini Live system instruction for the simulated caller."""
        p = self.persona
        lines = [
            "You are role-playing a HUMAN CALLER on a phone call with a voice assistant.",
            "You are NOT an assistant. Never offer help; you are the customer.",
            f"Speak only in the language/locale: {self.locale}.",
            "Keep every utterance short and natural like real phone speech (1-2 sentences).",
            "Never mention that you are an AI or a simulation.",
        ]
        if p.get("name"):
            lines.append(f"Your name: {p['name']}.")
        if p.get("brief"):
            lines.append(f"Who you are and why you are calling: {p['brief']}")
        if p.get("goals"):
            goals = "; ".join(str(g) for g in p["goals"])
            lines.append(f"Your goals for this call, in order: {goals}")
        if p.get("style"):
            lines.append(f"Speaking style: {p['style']}")
        if self.context.get("notes"):
            lines.append(f"Background context you know: {self.context['notes']}")
        if self.script_steps:
            lines.append(
                "Timed caller cues are injected automatically by the simulator while the agent speaks. "
                "Do NOT try to backchannel or interrupt on your own timing — stay quiet and listen unless "
                "you are answering a direct question after the agent finishes."
            )
        if self.run_spec.first_speaker == "agent":
            lines.append("Wait for the assistant to greet you first, then respond.")
        else:
            lines.append("You speak first: greet briefly and state why you are calling.")
        lines.append(
            "When all your goals are handled (or clearly cannot be), say a short goodbye "
            "and end with the exact token [END_CALL]."
        )
        return "\n".join(lines)


def parse_scenario(path: Path | str) -> Scenario:
    path = Path(path)
    if not path.exists():
        raise ScenarioError(f"Scenario file not found: {path}")

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()]
    records: list[tuple[int, dict[str, Any]]] = []
    for i, ln in enumerate(lines, start=1):
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as e:
            raise ScenarioError(f"{path}:{i}: invalid JSON — {e}") from e
        if not isinstance(obj, dict):
            raise ScenarioError(f"{path}:{i}: each line must be a JSON object")
        records.append((i, obj))

    if not records:
        raise ScenarioError(f"{path}: empty scenario file")

    header_line, header = records[0]
    if header.get("kind") != "Scenario":
        raise ScenarioError(f"{path}:{header_line}: first line must have kind=Scenario")
    if header.get("apiVersion") != API_VERSION:
        raise ScenarioError(
            f"{path}:{header_line}: apiVersion must be `{API_VERSION}` (got {header.get('apiVersion')!r})"
        )
    metadata = header.get("metadata") or {}
    scenario_id = metadata.get("id")
    if not scenario_id:
        raise ScenarioError(f"{path}:{header_line}: metadata.id is required")

    scenario = Scenario(
        id=str(scenario_id),
        path=path,
        locale=str(metadata.get("locale", "ja-JP")),
        tags=[str(t) for t in metadata.get("tags", [])],
    )

    for line_no, obj in records[1:]:
        kind = obj.get("kind")
        spec = obj.get("spec") or {}
        if kind not in KNOWN_KINDS:
            raise ScenarioError(f"{path}:{line_no}: unknown kind {kind!r} (expected one of {sorted(KNOWN_KINDS)})")
        if not isinstance(spec, dict):
            raise ScenarioError(f"{path}:{line_no}: spec must be an object")
        if kind == "Persona":
            scenario.persona = spec
        elif kind == "Context":
            scenario.context = spec
        elif kind == "Simulator":
            scenario.simulator = SimulatorSpec(
                max_turns=int(spec.get("max_turns", 6)),
                timeout_s=int(spec.get("timeout_s", 120)),
                first_speaker=str(spec.get("first_speaker", "agent")),
            )
        elif kind == "Execute":
            scenario.execute = ExecuteSpec(
                max_turns=int(spec["max_turns"]) if spec.get("max_turns") is not None else None,
                timeout_s=int(spec["timeout_s"]) if spec.get("timeout_s") is not None else None,
                first_speaker=str(spec["first_speaker"]) if spec.get("first_speaker") else None,
            )
        elif kind == "Dispatch":
            meta = spec.get("metadata")
            scenario.dispatch = DispatchSpec(
                metadata=str(meta).strip() if meta is not None and str(meta).strip() else None,
            )
        elif kind == "PassCriteria":
            scenario.pass_criteria = [str(c) for c in spec.get("criteria", [])]
        elif kind == "Script":
            from .script_parse import parse_script_steps, parse_script_verify

            try:
                scenario.script_steps = parse_script_steps(spec, f"{path}:{line_no}")
                scenario.script_verify = parse_script_verify(spec.get("verify"))
            except ValueError as e:
                raise ScenarioError(str(e)) from e

    if not scenario.persona.get("brief"):
        raise ScenarioError(f"{path}: Persona.spec.brief is required — the simulator needs a caller brief")
    if scenario.simulator.first_speaker not in ("agent", "user"):
        raise ScenarioError(f"{path}: Simulator.spec.first_speaker must be `agent` or `user`")
    run = scenario.run_spec
    if run.first_speaker not in ("agent", "user"):
        raise ScenarioError(f"{path}: Execute.spec.first_speaker must be `agent` or `user`")
    if scenario.dispatch and scenario.dispatch.metadata:
        try:
            json.loads(scenario.dispatch.metadata)
        except json.JSONDecodeError as e:
            raise ScenarioError(f"{path}: Dispatch.spec.metadata must be valid JSON string — {e}") from e

    return scenario


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Best-effort listing — invalid files are included with an `error` field."""
    out: list[dict[str, Any]] = []
    for f in sorted(scenarios_dir.glob("*.jsonl")):
        try:
            s = parse_scenario(f)
            out.append(
                {
                    "id": s.id,
                    "file": f.name,
                    "locale": s.locale,
                    "tags": s.tags,
                    "max_turns": s.run_spec.max_turns,
                    "first_speaker": s.run_spec.first_speaker,
                    "has_execute": s.execute is not None,
                    "has_dispatch": s.dispatch is not None and bool(s.dispatch.metadata),
                    "pass_criteria": len(s.pass_criteria),
                    "script_steps": len(s.script_steps),
                }
            )
        except ScenarioError as e:
            out.append({"id": None, "file": f.name, "error": str(e)})
    return out


def find_scenario(scenarios_dir: Path, scenario_id: str) -> Scenario:
    direct = scenarios_dir / f"{scenario_id}.jsonl"
    if direct.exists():
        return parse_scenario(direct)
    for f in scenarios_dir.glob("*.jsonl"):
        try:
            s = parse_scenario(f)
        except ScenarioError:
            continue
        if s.id == scenario_id:
            return s
    raise ScenarioError(
        f"Scenario `{scenario_id}` not found in {scenarios_dir} "
        f"(looked for {scenario_id}.jsonl and metadata.id match)"
    )
