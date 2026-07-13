"""JSONL scenario loader + validator.

A scenario file is line-delimited JSON. Line 1 is the header; every following line is
a section keyed by `kind`:

    {"apiVersion":"agent-sim/v1","kind":"Scenario","metadata":{"id":"smoke-hello","locale":"en-US","tags":["smoke"]}}
    {"kind":"Persona","spec":{"name":"Alex","brief":"...","goals":["..."],"style":"..."}}
    {"kind":"Context","spec":{"notes":"..."}}
    {"kind":"Simulator","spec":{"max_turns":6,"timeout_s":120,"first_speaker":"agent"}}
    {"kind":"Execute","spec":{"max_turns":2,"timeout_s":90,"first_speaker":"user"}}
    {"kind":"Dispatch","spec":{"metadata":"{\"yourProjectKey\":\"value\"}"}}
    {"kind":"Caller","spec":{"mode":"webrtc_sim"}}
    {"kind":"Telephony","spec":{"call_to":"+1555…","dial_in":"+1555…"}}
    {"kind":"PassCriteria","spec":{"criteria":["agent greets the caller politely"]}}
    {"kind":"Script","spec":{"steps":[...],"verify":{...}}}
    {"kind":"Script","spec":{"steps":[{"id":"backchannel","trigger":"agent_speaking","delay_ms":800,"say":"uh-huh","label":"backchannel-during-agent"}],"verify":{"require_during_agent_speech":true,"min_agent_finals_after_first_cue":1}}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asserts import AssertSpec, parse_assert_spec
from .script import ScriptStep, ScriptVerifySpec

API_VERSION = "agent-sim/v1"
KNOWN_KINDS = {
    "Persona",
    "Context",
    "Simulator",
    "Execute",
    "Dispatch",
    "PassCriteria",
    "Script",
    "Behavior",
    "Plugins",
    "Assert",
    "Caller",
    "Telephony",
}

CALLER_MODES = frozenset({"webrtc_sim", "inbound_sip", "outbound_sip", "agent_dials"})
SIP_MODES = frozenset({"inbound_sip", "outbound_sip", "agent_dials"})


def strip_extension_keys(obj: dict[str, Any]) -> dict[str, Any]:
    """Drop keys starting with ``_`` (e.g. ``_doc`` scaffold notes). Not part of the wire schema."""
    return {k: v for k, v in obj.items() if not str(k).startswith("_")}


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
class CallerSpec:
    """Per-scenario transport mode (never stored in shared config.yaml)."""

    mode: str = "webrtc_sim"


@dataclass
class TelephonySpec:
    """Per-scenario SIP dial params — overrides config.telephony when set."""

    call_to: str | None = None
    dial_in: str | None = None
    sip_trunk_id: str | None = None
    prepare_ms: int | None = None
    wait_until_answered: bool | None = None
    krisp_enabled: bool | None = None
    agent_room: str | None = None
    agent_room_name_template: str | None = None


@dataclass
class EffectiveTelephony:
    """Resolved telephony after scenario > config > built-in merge."""

    outbound_trunk_id: str | None
    inbound_trunk_id: str | None
    call_to: str | None
    dial_in: str | None
    prepare_ms: int
    wait_until_answered: bool
    krisp_enabled: bool
    agent_room: str | None
    agent_room_name_template: str | None


@dataclass
class Scenario:
    id: str
    path: Path
    locale: str = "en-US"
    tags: list[str] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    simulator: SimulatorSpec = field(default_factory=SimulatorSpec)
    execute: ExecuteSpec | None = None
    dispatch: DispatchSpec | None = None
    caller: CallerSpec | None = None
    telephony: TelephonySpec | None = None
    pass_criteria: list[str] = field(default_factory=list)
    script_steps: list[Any] = field(default_factory=list)
    script_verify: ScriptVerifySpec | None = None
    plugin_modules: list[str] = field(default_factory=list)
    asserts: AssertSpec | None = None
    # Raw Behavior.spec (Hamming-style policy); compiled into script_steps at parse end.
    behavior_spec: dict[str, Any] | None = None

    def effective_caller_mode(self) -> str:
        if self.caller and self.caller.mode:
            return self.caller.mode
        return "webrtc_sim"

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
            "caller_mode": self.effective_caller_mode(),
            "telephony_set": self.telephony is not None,
            "run": {
                "max_turns": self.run_spec.max_turns,
                "timeout_s": self.run_spec.timeout_s,
                "first_speaker": self.run_spec.first_speaker,
            },
            "pass_criteria": self.pass_criteria,
            "script_steps": len(self.script_steps),
            "plugin_modules": list(self.plugin_modules),
            "has_asserts": self.asserts is not None and not self.asserts.empty,
            "has_behavior": bool(self.behavior_spec),
            "constraints": (
                list(self.persona.get("constraints") or [])
                if isinstance(self.persona.get("constraints"), list)
                else (
                    [str(self.persona["constraints"])]
                    if self.persona.get("constraints")
                    else []
                )
            ),
            "speech_conditions": (
                self.persona.get("speech_conditions")
                if isinstance(self.persona.get("speech_conditions"), dict)
                else {}
            ),
            "script_verify": None
            if self.script_verify is None
            else {
                "require_during_agent_speech": self.script_verify.require_during_agent_speech,
                "min_agent_finals_after_first_cue": self.script_verify.min_agent_finals_after_first_cue,
                "min_user_finals_after_first_cue": self.script_verify.min_user_finals_after_first_cue,
                "min_interruptions": self.script_verify.min_interruptions,
                "max_interruptions": self.script_verify.max_interruptions,
                "min_agent_finals_after_silence": self.script_verify.min_agent_finals_after_silence,
                "min_agent_finals_after_barge_in": self.script_verify.min_agent_finals_after_barge_in,
                "plugins": list(self.script_verify.plugins),
            },
        }

    def effective_locale(self) -> str:
        """Locale for speech: Persona.language overrides Scenario.metadata.locale."""
        p_lang = self.persona.get("language") or self.persona.get("locale")
        return str(p_lang).strip() if p_lang else self.locale

    def persona_system_prompt(self) -> str:
        """Build the Gemini Live system instruction for the simulated caller."""
        p = self.persona
        locale = self.effective_locale()
        lines = [
            "You are role-playing a HUMAN CALLER on a phone call with a voice assistant.",
            "You are NOT an assistant. Never offer help; you are the customer.",
            f"Speak only in the language/locale: {locale}.",
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
        traits = p.get("traits") or p.get("behaviors") or []
        if isinstance(traits, str):
            traits = [traits]
        if traits:
            from .persona_traits import expand_traits

            lines.append(
                "Caller behavior traits (follow while staying natural): "
                + ", ".join(str(t) for t in traits)
            )
            lines.extend(expand_traits(traits))
        constraints = p.get("constraints") or []
        if isinstance(constraints, str):
            constraints = [constraints]
        constraints = [str(c).strip() for c in constraints if str(c).strip()]
        if constraints:
            lines.append("Hard constraints (do not violate):")
            for c in constraints:
                lines.append(f"- {c}")
        sc = p.get("speech_conditions") or p.get("speechConditions") or {}
        if isinstance(sc, dict) and sc:
            bits = []
            if sc.get("barge_policy"):
                bits.append(f"barge_policy={sc.get('barge_policy')}")
            if sc.get("silence_ms") or sc.get("user_silence_ms"):
                bits.append(
                    f"may go silent ~{sc.get('silence_ms') or sc.get('user_silence_ms')}ms "
                    "(simulator may enforce this)"
                )
            if sc.get("noise") or sc.get("ambient"):
                bits.append("there may be background noise on the line")
            if bits:
                lines.append("Speech conditions: " + "; ".join(bits) + ".")
        if self.context.get("notes"):
            lines.append(f"Background context you know: {self.context['notes']}")
        fixtures = self.context.get("fixtures")
        if isinstance(fixtures, dict) and fixtures:
            # Opaque hints for the caller (not parsed as business keys by core).
            lines.append(
                "You may know these test fixture hints (use only if natural): "
                + ", ".join(f"{k}={v}" for k, v in list(fixtures.items())[:12])
            )
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
        stripped = ln.strip()
        if not stripped:
            continue
        # Full-line guides (scaffold / human notes). Not JSON — ignored by runtime.
        if stripped.startswith("//"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ScenarioError(f"{path}:{i}: invalid JSON — {e}") from e
        if not isinstance(obj, dict):
            raise ScenarioError(f"{path}:{i}: each line must be a JSON object")
        records.append((i, strip_extension_keys(obj)))

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
        locale=str(metadata.get("locale", "en-US")),
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
        elif kind == "Caller":
            mode = str(spec.get("mode", "webrtc_sim")).strip().lower()
            if mode not in CALLER_MODES:
                raise ScenarioError(
                    f"{path}:{line_no}: Caller.spec.mode must be one of "
                    f"{sorted(CALLER_MODES)} (got {mode!r})"
                )
            scenario.caller = CallerSpec(mode=mode)
        elif kind == "Telephony":
            def _opt(key: str) -> str | None:
                v = spec.get(key)
                if v is None:
                    return None
                s = str(v).strip()
                return s or None

            prepare = spec.get("prepare_ms")
            wait = spec.get("wait_until_answered")
            krisp = spec.get("krisp_enabled")
            scenario.telephony = TelephonySpec(
                call_to=_opt("call_to"),
                dial_in=_opt("dial_in"),
                sip_trunk_id=_opt("sip_trunk_id") or _opt("outbound_trunk_id"),
                prepare_ms=int(prepare) if prepare is not None else None,
                wait_until_answered=bool(wait) if wait is not None else None,
                krisp_enabled=bool(krisp) if krisp is not None else None,
                agent_room=_opt("agent_room"),
                agent_room_name_template=_opt("agent_room_name_template"),
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
        elif kind == "Behavior":
            scenario.behavior_spec = dict(spec)
        elif kind == "Plugins":
            modules = spec.get("modules") or spec.get("load") or []
            if not isinstance(modules, list):
                raise ScenarioError(f"{path}:{line_no}: Plugins.spec.modules must be an array")
            scenario.plugin_modules.extend(str(m) for m in modules)
        elif kind == "Assert":
            try:
                scenario.asserts = parse_assert_spec(spec, f"{path}:{line_no}")
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

    mode = scenario.effective_caller_mode()
    if mode not in CALLER_MODES:
        raise ScenarioError(f"{path}: Caller.mode {mode!r} is not supported")
    if mode == "outbound_sip":
        has_call_to = bool(scenario.telephony and scenario.telephony.call_to)
        if not has_call_to:
            # Allowed: config telephony.sim_inbound_number may supply it at run time.
            pass
    if mode == "inbound_sip":
        has_dial_in = bool(scenario.telephony and scenario.telephony.dial_in)
        if not has_dial_in:
            pass  # config.telephony.dial_in may supply at run time

    # Hamming-style: compile speech_conditions + Behavior into Script (explicit Script wins by id).
    try:
        from .behavior_compile import apply_caller_behavior

        scenario.script_steps, scenario.script_verify = apply_caller_behavior(
            scenario.persona,
            scenario.behavior_spec,
            scenario.script_steps,
            scenario.script_verify,
            path_label=str(path),
        )
    except ValueError as e:
        raise ScenarioError(str(e)) from e

    return scenario


def effective_telephony(scenario: Scenario, cfg: Any) -> EffectiveTelephony:
    """Merge scenario Telephony over config.telephony (scenario wins when set)."""
    tel_cfg = getattr(cfg, "telephony", None)
    sc = scenario.telephony

    def pick_str(sc_val: str | None, cfg_val: str | None) -> str | None:
        if sc_val is not None and str(sc_val).strip():
            return str(sc_val).strip()
        if cfg_val is not None and str(cfg_val).strip():
            return str(cfg_val).strip()
        return None

    outbound = pick_str(
        sc.sip_trunk_id if sc else None,
        getattr(tel_cfg, "outbound_trunk_id", None) if tel_cfg else None,
    )
    inbound = pick_str(
        None,
        getattr(tel_cfg, "inbound_trunk_id", None) if tel_cfg else None,
    )
    call_to = pick_str(
        sc.call_to if sc else None,
        getattr(tel_cfg, "sim_inbound_number", None) if tel_cfg else None,
    )
    dial_in = pick_str(
        sc.dial_in if sc else None,
        getattr(tel_cfg, "dial_in", None) if tel_cfg else None,
    )
    prepare_ms = 3000
    if tel_cfg is not None and getattr(tel_cfg, "prepare_ms", None) is not None:
        prepare_ms = int(tel_cfg.prepare_ms)
    if sc is not None and sc.prepare_ms is not None:
        prepare_ms = int(sc.prepare_ms)

    wait_answered = True
    if tel_cfg is not None:
        wait_answered = bool(getattr(tel_cfg, "wait_until_answered", True))
    if sc is not None and sc.wait_until_answered is not None:
        wait_answered = bool(sc.wait_until_answered)

    krisp = False
    if tel_cfg is not None:
        krisp = bool(getattr(tel_cfg, "krisp_enabled", False))
    if sc is not None and sc.krisp_enabled is not None:
        krisp = bool(sc.krisp_enabled)

    agent_room = pick_str(
        sc.agent_room if sc else None,
        getattr(tel_cfg, "agent_room", None) if tel_cfg else None,
    )
    agent_room_tmpl = pick_str(
        sc.agent_room_name_template if sc else None,
        getattr(tel_cfg, "agent_room_name_template", None) if tel_cfg else None,
    )
    return EffectiveTelephony(
        outbound_trunk_id=outbound,
        inbound_trunk_id=inbound,
        call_to=call_to,
        dial_in=dial_in,
        prepare_ms=prepare_ms,
        wait_until_answered=wait_answered,
        krisp_enabled=krisp,
        agent_room=agent_room,
        agent_room_name_template=agent_room_tmpl,
    )


def validate_telephony_for_mode(scenario: Scenario, cfg: Any) -> None:
    """Fail-fast if SIP mode is missing required trunk/number after merge."""
    mode = scenario.effective_caller_mode()
    if mode not in SIP_MODES:
        return
    tel = effective_telephony(scenario, cfg)
    if mode in ("outbound_sip", "inbound_sip") and not tel.outbound_trunk_id:
        raise ScenarioError(
            f"Scenario `{scenario.id}` mode={mode} requires telephony.outbound_trunk_id "
            f"in config or Telephony.sip_trunk_id in the scenario."
        )
    if mode == "outbound_sip" and not tel.call_to:
        raise ScenarioError(
            f"Scenario `{scenario.id}` mode=outbound_sip requires Telephony.call_to "
            f"or config telephony.sim_inbound_number (number/DID Gemini answers)."
        )
    if mode == "inbound_sip" and not tel.dial_in:
        raise ScenarioError(
            f"Scenario `{scenario.id}` mode=inbound_sip requires Telephony.dial_in "
            f"or config telephony.dial_in (agent-side inbound DID)."
        )


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
                    "caller_mode": s.effective_caller_mode(),
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
