"""`lk-sim` CLI — same public ops as the MCP server (see ops module docstring).

Defaults project root to CWD; use `--root` for another target repo.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


_ensure_utf8_stdio()

from . import ops
from .config import ConfigError
from .scenario import ScenarioError

app = typer.Typer(
    name="lk-sim",
    help="Simulate an AI caller against a LiveKit voice agent (parity with MCP tools).",
)

ROOT_OPTION = typer.Option(None, "--root", help="Project root (default: current directory)")


def _root(root: Optional[Path]) -> Path:
    return (root or Path.cwd()).resolve()


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _run_failed(result: dict[str, Any]) -> bool:
    if not result.get("executed") or result.get("status") != "done":
        return True
    verdict = ((result.get("summary") or {}).get("verdict") or {}).get("verdict")
    return verdict == "fail"


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except (ConfigError, ScenarioError, RuntimeError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def init(root: Optional[Path] = ROOT_OPTION) -> None:
    """Scaffold .agent-sim/ in the target repo. (MCP: init_project)"""
    _print(ops.init_project(_root(root)))


@app.command()
def guide() -> None:
    """Print setup/ops guide for agents and humans. (MCP: guide)"""
    typer.echo(ops.guide()["text"])


@app.command()
def web(
    run_id: Optional[str] = typer.Argument(
        None,
        help="Run id under .agent-sim/reports/ (default: newest)",
    ),
    port: int = typer.Option(8765, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Local report player: audio + transcript sync while playing. (MCP: web)"""
    try:
        typer.secho("Starting report UI — Ctrl+C to stop", fg=typer.colors.GREEN)
        info = ops.web(
            _root(root),
            run_id=run_id,
            host=host,
            port=port,
            open_browser=not no_open,
            blocking=True,
        )
        # blocking returns after shutdown; print was useful if non-blocking
        _print({k: v for k, v in info.items() if k not in ("server", "thread")})
    except (ConfigError, FileNotFoundError, OSError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def preflight(
    root: Optional[Path] = ROOT_OPTION,
    no_connectivity: bool = typer.Option(False, "--no-connectivity", help="Skip LiveKit API check"),
) -> None:
    """Check config + LiveKit connectivity without running a scenario. (MCP: preflight)"""
    result = _run(ops.preflight(_root(root), connectivity=not no_connectivity))
    _print(result)
    if not result.get("ok"):
        raise typer.Exit(1)


@app.command("scenarios")
def scenarios_cmd(root: Optional[Path] = ROOT_OPTION) -> None:
    """List scenarios. (MCP: list_scenarios)"""
    try:
        _print(ops.list_scenarios(_root(root)))
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def plugins(root: Optional[Path] = ROOT_OPTION) -> None:
    """List verify plugins. (MCP: list_plugins)"""
    try:
        _print(ops.list_plugins(_root(root)))
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def validate(scenario_id: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Validate one scenario. (MCP: validate_scenario)"""
    result = ops.validate_scenario(_root(root), scenario_id)
    _print(result)
    if not result.get("valid"):
        raise typer.Exit(1)


@app.command()
def export(scenario_id: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Export parsed scenario JSON. (MCP: export_scenario)"""
    try:
        _print(ops.export_scenario(_root(root), scenario_id))
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command("scenario-init")
def scenario_init_cmd(
    scenario_id: str = typer.Argument(..., help="New scenario id (filename without .jsonl)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Scaffold scenario JSONL with // guide comments + examples. (MCP: init_scenario)"""
    try:
        _print(ops.init_scenario(_root(root), scenario_id, force=force))
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def execute(scenario_id: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Validate then execute one scenario from .agent-sim/scenarios/. (MCP: execute_scenario)"""
    result = _run(ops.execute_scenario(_root(root), scenario_id))
    _print(result)
    if _run_failed(result):
        raise typer.Exit(1)


@app.command("execute-all")
def execute_all_cmd(
    scenario_ids: Optional[list[str]] = typer.Argument(
        None,
        help="Optional scenario ids; omit to run all valid scenarios",
    ),
    tag: Optional[str] = typer.Option(None, help="Only scenarios with this tag (when ids omitted)"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Execute multiple scenarios. (MCP: execute_scenarios)"""
    result = _run(
        ops.execute_scenarios(
            _root(root),
            scenario_ids=list(scenario_ids) if scenario_ids else None,
            tag=tag,
        )
    )
    _print(result)
    if any(_run_failed(r) for r in result.get("results", [])):
        raise typer.Exit(1)


@app.command("execute-dict")
def execute_dict_cmd(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="JSON file with scenario dict; omit to read JSON from stdin",
    ),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Validate then run an in-memory scenario JSON. (MCP: execute_scenario_dict)"""
    try:
        if file is not None:
            scenario = json.loads(file.read_text(encoding="utf-8"))
        else:
            scenario = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as e:
        typer.secho(f"Invalid scenario JSON: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not isinstance(scenario, dict):
        typer.secho("Scenario JSON must be an object", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    result = _run(ops.execute_scenario_dict(_root(root), scenario))
    _print(result)
    if _run_failed(result):
        raise typer.Exit(1)


@app.command()
def status(run_id: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Run status from SQLite. (MCP: get_run_status)"""
    _print(_run(ops.get_run_status(_root(root), run_id)))


@app.command()
def log(
    run_id: str,
    kind: Optional[str] = typer.Option(None, help="Event kind, trailing * for prefix (tool.*)"),
    turn: Optional[int] = typer.Option(None),
    source: Optional[str] = typer.Option(None),
    since_mono_ms: Optional[int] = typer.Option(None, help="Only events at/after this mono ms"),
    limit: int = typer.Option(200),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Filtered view of events.jsonl. (MCP: get_run_log)"""
    try:
        _print(
            ops.get_run_log(
                _root(root),
                run_id,
                kind=kind,
                turn=turn,
                source=source,
                since_mono_ms=since_mono_ms,
                limit=limit,
            )
        )
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def report(run_id: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Summary + verdict + suspicious turns. (MCP: get_run_report)"""
    _print(_run(ops.get_run_report(_root(root), run_id)))


@app.command()
def compare(run_id_a: str, run_id_b: str, root: Optional[Path] = ROOT_OPTION) -> None:
    """Diff two runs. (MCP: compare_runs)"""
    _print(_run(ops.compare_runs(_root(root), run_id_a, run_id_b)))


@app.command()
def runs(
    limit: int = typer.Option(20),
    scenario_id: Optional[str] = typer.Option(None, "--scenario"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Run history, newest first. (MCP: list_runs)"""
    _print(_run(ops.list_runs(_root(root), limit=limit, scenario_id=scenario_id)))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
