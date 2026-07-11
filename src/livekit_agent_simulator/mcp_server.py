"""FastMCP server — same public ops as `lk-sim` CLI (see ops module docstring).

Every tool takes `project_root`: absolute path of the repo under test that
contains (or will contain) the `.agent-sim/` folder.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from . import ops

mcp = FastMCP(
    "livekit-agent-simulator",
    instructions=(
        "Simulate a realtime AI caller against a LiveKit voice agent and inspect the "
        "forensic behavior log. Typical flow: guide → init_project → edit .agent-sim/config.yaml → "
        "preflight → init_scenario / list_scenarios → execute_scenario → get_run_report / get_run_log."
    ),
)


@mcp.tool
def guide() -> dict[str, Any]:
    """Setup and ops guide for coding agents (markdown text). Read before first setup if unfamiliar."""
    return ops.guide()


@mcp.tool
def web(
    project_root: str,
    run_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Start local report player (audio + transcript sync). Returns URL; server runs in background until process exits."""
    return ops.web(
        project_root,
        run_id=run_id,
        host=host,
        port=port,
        open_browser=open_browser,
        blocking=False,
    )


@mcp.tool
def init_project(project_root: str) -> dict[str, Any]:
    """Scaffold `.agent-sim/` (config.yaml + smoke scenario) in the target repo and gitignore it."""
    return ops.init_project(project_root)


@mcp.tool
async def preflight(project_root: str, connectivity: bool = True) -> dict[str, Any]:
    """Check config + folders + optional LiveKit API connectivity without running a scenario."""
    return await ops.preflight(project_root, connectivity=connectivity)


@mcp.tool
def list_scenarios(project_root: str) -> list[dict[str, Any]]:
    """List all scenarios in `.agent-sim/scenarios/*.jsonl` with id, tags, and validity."""
    return ops.list_scenarios(project_root)


@mcp.tool
def list_plugins(project_root: str) -> dict[str, Any]:
    """List registered verify plugins and local `.agent-sim/plugins/*.py` modules."""
    return ops.list_plugins(project_root)


@mcp.tool
def validate_scenario(project_root: str, scenario_id: str) -> dict[str, Any]:
    """Validate a scenario file: schema, required Persona brief, PassCriteria lint."""
    return ops.validate_scenario(project_root, scenario_id)


@mcp.tool
def export_scenario(project_root: str, scenario_id: str) -> dict[str, Any]:
    """Export a parsed scenario (Persona, Execute, Dispatch flag, PassCriteria) as JSON."""
    return ops.export_scenario(project_root, scenario_id)


@mcp.tool
def init_scenario(project_root: str, scenario_id: str, force: bool = False) -> dict[str, Any]:
    """Scaffold `.agent-sim/scenarios/<id>.jsonl` with `//` guide lines + example JSON kinds. Runtime skips `//` lines."""
    return ops.init_scenario(project_root, scenario_id, force=force)


@mcp.tool
async def execute_scenario(project_root: str, scenario_id: str) -> dict[str, Any]:
    """Validate then execute one scenario from `.agent-sim/scenarios/*.jsonl`."""
    return await ops.execute_scenario(project_root, scenario_id)


@mcp.tool
async def execute_scenarios(
    project_root: str,
    scenario_ids: list[str] | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Execute multiple scenarios. Omit scenario_ids to run all valid files; optional tag filter."""
    return await ops.execute_scenarios(project_root, scenario_ids=scenario_ids, tag=tag)


@mcp.tool
async def execute_scenario_dict(project_root: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Validate then run an in-memory scenario dict (no JSONL file). Same fields as export_scenario."""
    return await ops.execute_scenario_dict(project_root, scenario)


@mcp.tool
async def get_run_status(project_root: str, run_id: str) -> dict[str, Any]:
    """Status of a run from SQLite: running / done / failed, turn count, duration."""
    return await ops.get_run_status(project_root, run_id)


@mcp.tool
def get_run_log(
    project_root: str,
    run_id: str,
    kind: str | None = None,
    turn: int | None = None,
    source: str | None = None,
    since_mono_ms: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Read events.jsonl with filters. `kind` supports trailing `*` prefix match (e.g. `tool.*`)."""
    return ops.get_run_log(
        project_root,
        run_id,
        kind=kind,
        turn=turn,
        source=source,
        since_mono_ms=since_mono_ms,
        limit=limit,
    )


@mcp.tool
async def get_run_report(project_root: str, run_id: str) -> dict[str, Any]:
    """Full report: summary, judge verdict, suspicious turns, paths to timeline/events/audio."""
    return await ops.get_run_report(project_root, run_id)


@mcp.tool
async def compare_runs(project_root: str, run_id_a: str, run_id_b: str) -> dict[str, Any]:
    """Diff two runs: duration, turns, tool errors, turn-taking percentiles, verdicts."""
    return await ops.compare_runs(project_root, run_id_a, run_id_b)


@mcp.tool
async def list_runs(
    project_root: str, limit: int = 20, scenario_id: str | None = None
) -> list[dict[str, Any]]:
    """Run history from SQLite, newest first. Optionally filter by scenario_id."""
    return await ops.list_runs(project_root, limit=limit, scenario_id=scenario_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
