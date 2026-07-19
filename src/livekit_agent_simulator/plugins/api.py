"""Plugin API types — verify hooks devs register and reference from scenario JSONL."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from ..scenario import Scenario
    from ..script import ScriptStep, ScriptVerifySpec

VerifyResult = dict[str, Any]


class VerifyPlugin(Protocol):
    """Post-run check over events.jsonl. Return shape: {pass: bool, checks?: [...]}."""

    def __call__(self, ctx: VerifyContext) -> VerifyResult: ...


SetupFn = Callable[[], None]

BeforeRunHook = Callable[["BeforeRunContext"], None]
AfterRunHook = Callable[["AfterRunContext"], None]


@dataclass(frozen=True)
class BeforeRunContext:
    """Context passed to before_run hooks — scenario loaded, about to connect."""

    scenario: Scenario
    project_root: Path
    run_id: str
    run_name: str | None
    meta: dict[str, Any]
    dispatch_metadata: dict[str, Any] | None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AfterRunContext:
    """Context passed to after_run hooks — run finished (done or failed)."""

    scenario: Scenario
    project_root: Path
    run_id: str
    run_name: str | None
    report_dir: Path
    status: str
    summary: dict[str, Any]
    events: list[dict[str, Any]]
    verdict: dict[str, Any] | None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifyContext:
    """Input to a registered verify plugin after a sim run completes."""

    events: list[dict[str, Any]]
    steps: list[ScriptStep]
    verify: ScriptVerifySpec
    scenario: Scenario
    project_root: Path
    plugin_name: str
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def scenario_id(self) -> str:
        return self.scenario.id

    def events_of_kind(self, kind: str, *, prefix: bool = False) -> list[dict[str, Any]]:
        if prefix:
            return [e for e in self.events if str(e.get("kind", "")).startswith(kind)]
        return [e for e in self.events if e.get("kind") == kind]

    def first_cue_ms(self) -> int | None:
        cues = self.events_of_kind("sim.script.cue")
        return int(cues[0]["ts_mono_ms"]) if cues else None

    def finals_after_first_cue(self, role: str) -> int:
        cue_ms = self.first_cue_ms()
        if cue_ms is None:
            return 0
        kind = f"transcript.{role}.final"
        return sum(1 for e in self.events_of_kind(kind) if int(e.get("ts_mono_ms", 0)) >= cue_ms)
