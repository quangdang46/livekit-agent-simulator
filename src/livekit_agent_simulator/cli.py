"""`lks` CLI (alias: `lk-sim`) — same public ops as the MCP server (see ops module docstring).

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
    name="lks",
    help="Simulate an AI caller against a LiveKit voice agent (parity with MCP tools). Alias: lk-sim.",
)

ROOT_OPTION = typer.Option(None, "--root", help="Project root (default: current directory)")


def _root(root: Optional[Path]) -> Path:
    return (root or Path.cwd()).resolve()


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _run_failed(result: dict[str, Any], *, strict_judge: bool = False) -> bool:
    """CI gate: hard fails on status/assert/script; judge only if strict_judge."""
    from .suite import evaluate_run_result

    # Suite payload from execute-all
    if result.get("suite") and isinstance(result.get("suite"), dict):
        return not bool(result["suite"].get("ok"))
    return not evaluate_run_result(result, strict_judge=strict_judge)["ok"]


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        typer.secho("\nInterrupted — stopping.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(130)
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
        help="Run id under .agent-sim/reports/ (default: home list of all runs)",
    ),
    port: int = typer.Option(8765, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Local report player: audio + transcript sync while playing. (MCP: web)"""
    try:
        typer.echo("Starting report UI — Ctrl+C to stop")
        info = ops.web(
            _root(root),
            run_id=run_id,
            host=host,
            port=port,
            open_browser=not no_open,
            blocking=True,
        )
        _print({k: v for k, v in info.items() if k not in ("server", "thread")})
    except KeyboardInterrupt:
        typer.echo("\nStopped report UI.")
        raise typer.Exit(0)
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
def cues(
    root: Optional[Path] = ROOT_OPTION,
    asset: Optional[str] = typer.Option(
        None,
        "--resolve",
        help="Resolve one asset id/path and print path (builtin:voice.barge_short, builtin:noise.loud, my.wav, …)",
    ),
) -> None:
    """List built-in + target room_pcm cues. (MCP: list_cues)"""
    from .audio.cue_catalog import describe_resolution
    from .config import load_config

    r = _root(root)
    if asset:
        try:
            cfg = load_config(r)
            _print(
                describe_resolution(
                    asset, project_root=cfg.project_root, cues_config=cfg.cues
                )
            )
        except ConfigError:
            _print(describe_resolution(asset, project_root=r if (r / ".agent-sim").is_dir() else None))
        return
    try:
        _print(ops.list_cues(r))
    except Exception as e:
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
def execute(
    scenario_id: str,
    root: Optional[Path] = ROOT_OPTION,
    strict_judge: bool = typer.Option(
        False,
        "--strict-judge",
        help="Also fail CI exit if LLM PassCriteria judge verdict is fail",
    ),
    repeat: int = typer.Option(
        1,
        "--repeat",
        "-n",
        help="Run scenario N times for flake control (pass@k)",
    ),
    pass_at_k: Optional[int] = typer.Option(
        None,
        "--pass-at-k",
        "-k",
        help="Minimum hard-pass iterations (default = repeat). Example: --repeat 5 --pass-at-k 3",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Override slug after seq prefix (e.g. demo → reports/001-demo/)",
    ),
) -> None:
    """Validate then execute one scenario from .agent-sim/scenarios/. (MCP: execute_scenario)"""
    result = _run(
        ops.execute_scenario(
            _root(root),
            scenario_id,
            repeat=repeat,
            pass_at_k=pass_at_k,
            run_name=name,
        )
    )
    from .suite import evaluate_run_result

    gate = evaluate_run_result(result, strict_judge=strict_judge)
    result = {**result, "gate": gate}
    _print(result)
    if _run_failed(result, strict_judge=strict_judge):
        raise typer.Exit(1)


@app.command("execute-all")
def execute_all_cmd(
    scenario_ids: Optional[list[str]] = typer.Argument(
        None,
        help="Optional scenario ids; omit to run all valid scenarios",
    ),
    tag: Optional[str] = typer.Option(None, help="Only scenarios with this tag (when ids omitted)"),
    strict_judge: bool = typer.Option(
        False,
        "--strict-judge",
        help="Fail suite if any LLM judge verdict is fail (default: hard gates only)",
    ),
    no_report: bool = typer.Option(
        False,
        "--no-report",
        help="Do not write suite-*.json/md under .agent-sim/reports/",
    ),
    repeat: int = typer.Option(
        1,
        "--repeat",
        "-n",
        help="Repeat each scenario N times for flake control (pass@k)",
    ),
    pass_at_k: Optional[int] = typer.Option(
        None,
        "--pass-at-k",
        "-k",
        help="Minimum hard-pass iterations per scenario (default = repeat)",
    ),
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-p",
        help="Run up to N scenarios at once (default 1 = sequential). "
        "Within each scenario, --repeat stays sequential.",
    ),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Execute multiple scenarios; print suite matrix + CI gate. (MCP: execute_scenarios)"""
    result = _run(
        ops.execute_scenarios(
            _root(root),
            scenario_ids=list(scenario_ids) if scenario_ids else None,
            tag=tag,
            strict_judge=strict_judge,
            write_report=not no_report,
            repeat=repeat,
            pass_at_k=pass_at_k,
            parallel=parallel,
        )
    )
    _print(result)
    if _run_failed(result, strict_judge=strict_judge):
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
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Override slug after seq prefix (e.g. demo → reports/001-demo/)",
    ),
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
    result = _run(ops.execute_scenario_dict(_root(root), scenario, run_name=name))
    from .suite import evaluate_run_result

    gate = evaluate_run_result(result, strict_judge=False)
    result = {**result, "gate": gate}
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
    """Summary + verdict + suspicious turns (includes caller.behavior_summary). (MCP: get_run_report)"""
    _print(_run(ops.get_run_report(_root(root), run_id)))


@app.command()
def compare(
    run_id_a: str = typer.Argument(..., help="Baseline run id when --baseline, else run A"),
    run_id_b: str = typer.Argument(..., help="Candidate run id when --baseline, else run B"),
    baseline: bool = typer.Option(
        False,
        "--baseline",
        help="Treat run_id_a as golden baseline; attach hard regression gate (CI exit 1 if fail)",
    ),
    max_ttfw_regression_ms: float = typer.Option(
        1500.0, "--max-ttfw-regression-ms", help="Max allowed TTFW increase vs baseline"
    ),
    max_turn_p95_regression_ms: float = typer.Option(
        2000.0, "--max-turn-p95-regression-ms", help="Max allowed turn p95 increase vs baseline"
    ),
    max_duration_regression_ms: float = typer.Option(
        30000.0, "--max-duration-regression-ms", help="Max allowed duration increase vs baseline"
    ),
    max_barge_recovery_drop: float = typer.Option(
        0.0,
        "--max-barge-recovery-drop",
        help="Max allowed barge_recovery_rate drop vs baseline (0 = no drop)",
    ),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Diff two runs. With --baseline, hard-fail on latency/assert regression. (MCP: compare_runs)"""
    if baseline:
        result = _run(
            ops.compare_runs_with_baseline(
                _root(root),
                run_id_a,
                run_id_b,
                max_ttfw_regression_ms=max_ttfw_regression_ms,
                max_turn_p95_regression_ms=max_turn_p95_regression_ms,
                max_duration_regression_ms=max_duration_regression_ms,
                max_barge_recovery_drop=max_barge_recovery_drop,
            )
        )
        _print(result)
        gate = result.get("gate") if isinstance(result, dict) else None
        if isinstance(gate, dict) and not gate.get("ok", True):
            raise typer.Exit(code=1)
        return
    _print(_run(ops.compare_runs(_root(root), run_id_a, run_id_b)))


@app.command()
def runs(
    limit: int = typer.Option(20),
    scenario_id: Optional[str] = typer.Option(None, "--scenario"),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Run history, newest first. (MCP: list_runs)"""
    _print(_run(ops.list_runs(_root(root), limit=limit, scenario_id=scenario_id)))


@app.command("scenario-from-run")
def scenario_from_run_cmd(
    run_id: str = typer.Argument(..., help="Run ID to promote"),
    scenario_id: Optional[str] = typer.Option(
        None,
        "--id",
        help="Override draft scenario id (default: auto from source)")
        ,
    write: bool = typer.Option(
        False,
        "--write",
        "-w",
        help="Write draft .jsonl to .agent-sim/scenarios/",
    ),
    root: Optional[Path] = ROOT_OPTION,
) -> None:
    """Promote a finished run into a draft scenario JSONL (fail → golden). (MCP: scenario_from_run)"""
    try:
        _print(ops.scenario_from_run(_root(root), run_id, scenario_id=scenario_id, write=write))
    except (ConfigError, FileNotFoundError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command("mcp")
def mcp_serve() -> None:
    """Start the MCP server (stdio). Same tools as CLI ops — for Cursor / Claude / etc.

    Config example::

        {
          "mcpServers": {
            "livekit-agent-simulator": {
              "command": "lks",
              "args": ["mcp"]
            }
          }
        }
    """
    from .mcp_server import main as mcp_main

    mcp_main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()

