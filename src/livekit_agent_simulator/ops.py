"""Shared project operations — single surface for MCP + CLI.

Public ops (both surfaces expose these, same semantics):

    init_project, preflight, guide, web,
    list_scenarios, list_plugins, list_cues, validate_scenario, export_scenario, init_scenario,
    execute_scenario, execute_scenarios, execute_scenario_dict, scenario_from_run,
    get_run_status, get_run_log, get_run_report, compare_runs, list_runs
    # execute_scenarios also returns suite matrix + CI gate (see suite.py)

Internal helpers (not exposed on CLI/MCP): ``_run_scenario``, ``_run_scenario_dict``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config import DOT_FOLDER, ConfigError, load_config
from .logging.sqlite_store import RunStore
from .paths import package_templates_dir
from .scenario import ScenarioError, find_scenario, list_scenarios as _list_scenarios, parse_scenario
from .scenario_from_dict import export_scenario_dict, scenario_from_dict
from . import run_orchestrator
from .preflight import run_preflight
from .plugins.loader import ensure_plugins_loaded
from .plugins.registry import list_verify_plugins

_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def init_project(project_root: Path | str) -> dict[str, Any]:
    """Scaffold .agent-sim/ with config.yaml template + smoke scenario; gitignore it."""
    root = Path(project_root).resolve()
    templates = package_templates_dir()
    dot = root / DOT_FOLDER
    created: list[str] = []

    (dot / "scenarios").mkdir(parents=True, exist_ok=True)
    (dot / "reports").mkdir(parents=True, exist_ok=True)
    (dot / "plugins").mkdir(parents=True, exist_ok=True)
    (dot / "cues").mkdir(parents=True, exist_ok=True)
    cues_readme = dot / "cues" / "README.md"
    if not cues_readme.exists():
        cues_readme.write_text(
            "# Target audio cues (room_pcm)\n\n"
            "Drop **PCM16 mono @ 24 kHz** WAVs here to override package built-ins "
            "or add project-specific noise.\n\n"
            "Scenario: `\"delivery\":\"room_pcm\",\"asset\":\"my_noise.wav\"` "
            "or `\"asset\":\"builtin:noise.loud\"`.\n\n"
            "List: `lk-sim cues --root .`\n",
            encoding="utf-8",
        )
        created.append(str(cues_readme))

    config_dst = dot / "config.yaml"
    if not config_dst.exists():
        shutil.copyfile(templates / "config.yaml", config_dst)
        created.append(str(config_dst))

    smoke_dst = dot / "scenarios" / "smoke-hello.jsonl"
    if not smoke_dst.exists():
        shutil.copyfile(templates / "smoke-hello.jsonl", smoke_dst)
        created.append(str(smoke_dst))

    plugin_dst = dot / "plugins" / "example_verify.py"
    if not plugin_dst.exists():
        shutil.copyfile(templates / "plugins" / "example_verify.py", plugin_dst)
        created.append(str(plugin_dst))

    gitignore = root / ".gitignore"
    line = f"{DOT_FOLDER}/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if line not in content.splitlines():
            gitignore.write_text(content.rstrip("\n") + f"\n\n# livekit-agent-simulator\n{line}\n", encoding="utf-8")
            created.append(f"{gitignore} (+{line})")
    else:
        gitignore.write_text(f"# livekit-agent-simulator\n{line}\n", encoding="utf-8")
        created.append(str(gitignore))

    return {
        "dot_dir": str(dot),
        "created": created,
        "next_steps": [
            f"Fill in LiveKit + Google credentials in {config_dst}",
            "Make sure your worker is running with the configured agent_name",
            "Run the smoke scenario: lk-sim execute smoke-hello",
        ],
    }


async def preflight(project_root: Path | str, connectivity: bool = True) -> dict[str, Any]:
    """Config + folder + optional LiveKit API check. Returns {ok, checks}."""
    result, _ = await run_preflight(project_root, connectivity=connectivity)
    return {"ok": result.ok, "checks": result.checks}


def guide() -> dict[str, Any]:
    """On-demand setup / ops guide for humans and coding agents (no project_root required)."""
    path = package_templates_dir() / "GUIDE.md"
    if not path.exists():
        raise ConfigError(f"Package guide missing: {path}")
    return {
        "path": str(path),
        "text": path.read_text(encoding="utf-8"),
    }


def init_scenario(
    project_root: Path | str,
    scenario_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Scaffold ``.agent-sim/scenarios/<id>.jsonl`` with ``//`` guide lines + example JSON.

    Full-line ``//`` comments are ignored at parse time. Delete unused kind lines as needed.
    """
    scenario_id = scenario_id.strip()
    if not _SCENARIO_ID_RE.match(scenario_id):
        raise ConfigError(
            f"Invalid scenario_id {scenario_id!r}: use letters/digits/[_-], start with alnum, max 64 chars"
        )

    root = Path(project_root).resolve()
    # Prefer target .agent-sim if already initialized; else create scenarios dir only.
    try:
        cfg = load_config(root)
        scenarios_dir = cfg.scenarios_dir
    except ConfigError:
        scenarios_dir = root / DOT_FOLDER / "scenarios"
        scenarios_dir.mkdir(parents=True, exist_ok=True)

    dest = scenarios_dir / f"{scenario_id}.jsonl"
    if dest.exists() and not force:
        raise ConfigError(
            f"{dest} already exists. Pass force=true / --force to overwrite, or pick another id."
        )

    scaffold = package_templates_dir() / "scenario-scaffold.jsonl"
    if not scaffold.exists():
        raise ConfigError(f"Package scaffold missing: {scaffold}")
    text = scaffold.read_text(encoding="utf-8").replace("{{SCENARIO_ID}}", scenario_id)
    dest.write_text(text, encoding="utf-8")

    # Ensure the scaffold still parses after id substitution.
    try:
        parse_scenario(dest)
    except ScenarioError as e:
        dest.unlink(missing_ok=True)
        raise ConfigError(f"Scaffold failed validation: {e}") from e

    return {
        "path": str(dest),
        "scenario_id": scenario_id,
        "created": True,
        "overwritten": force,
        "next_steps": [
            f"Edit {dest} — // lines are guides; remove unused kind JSON lines",
            f"Validate: lk-sim validate {scenario_id} --root {root}",
            f"Run: lk-sim execute {scenario_id} --root {root}",
        ],
    }


def list_scenarios(project_root: Path | str) -> list[dict[str, Any]]:
    cfg = load_config(project_root)
    return _list_scenarios(cfg.scenarios_dir)


def validate_scenario(project_root: Path | str, scenario_id: str) -> dict[str, Any]:
    cfg = load_config(project_root)
    path = cfg.scenarios_dir / f"{scenario_id}.jsonl"
    if not path.exists():
        candidates = list(cfg.scenarios_dir.glob("*.jsonl"))
        return {
            "valid": False,
            "error": f"{path} not found",
            "available": [c.name for c in candidates],
        }
    try:
        s = parse_scenario(path)
    except ScenarioError as e:
        return {"valid": False, "error": str(e)}
    warnings: list[str] = []
    if not s.pass_criteria:
        warnings.append("No PassCriteria — judge will be skipped for this scenario.")
    run = s.run_spec
    if run.max_turns > 20:
        warnings.append(f"max_turns={run.max_turns} is unusually high.")
    if run.first_speaker == "agent" and not (s.dispatch and s.dispatch.metadata):
        warnings.append(
            "first_speaker=agent with no Dispatch.metadata — many agents wait for caller audio. "
            "Add Execute.first_speaker=user or a project-specific Dispatch.metadata JSON."
        )
    if s.plugin_modules or (s.script_verify and s.script_verify.plugins):
        load_info = ensure_plugins_loaded(cfg.project_root, s.plugin_modules)
        known = set(load_info.get("verify_plugins") or [])
        for name in (s.script_verify.plugins if s.script_verify else ()):
            if name not in known:
                warnings.append(f"verify plugin {name!r} is not registered (load errors: {load_info.get('errors')})")
        if load_info.get("errors"):
            warnings.extend(f"plugin load: {e}" for e in load_info["errors"])
    return {
        "valid": True,
        "id": s.id,
        "locale": s.locale,
        "max_turns": run.max_turns,
        "timeout_s": run.timeout_s,
        "first_speaker": run.first_speaker,
        "has_execute": s.execute is not None,
        "has_dispatch": s.dispatch is not None and bool(s.dispatch.metadata),
        "pass_criteria": s.pass_criteria,
        "warnings": warnings,
    }


def export_scenario(project_root: Path | str, scenario_id: str) -> dict[str, Any]:
    """Export parsed scenario JSON for inspection / agent-driven customization."""
    cfg = load_config(project_root)
    try:
        s = find_scenario(cfg.scenarios_dir, scenario_id)
    except ScenarioError as e:
        return {"found": False, "scenario_id": scenario_id, "error": str(e)}
    return {"found": True, **export_scenario_dict(s)}


def list_plugins(project_root: Path | str) -> dict[str, Any]:
    """List registered verify plugins after loading entry-points and local modules."""
    cfg = load_config(project_root)
    local_dir = cfg.dot_dir / "plugins"
    local_files = sorted(p.stem for p in local_dir.glob("*.py") if not p.name.startswith("_"))
    load_info = ensure_plugins_loaded(cfg.project_root, local_files)
    return {
        "verify_plugins": list_verify_plugins(),
        "local_modules": local_files,
        "load": load_info,
        "entry_point_group": "lk_sim.plugins",
    }


def list_cues(project_root: Path | str | None = None) -> dict[str, Any]:
    """List built-in + target room_pcm cues (and config aliases if root has config)."""
    from .audio.cue_catalog import describe_resolution, list_all_cues

    if project_root is None:
        return list_all_cues(None)
    root = Path(project_root).resolve()
    try:
        cfg = load_config(root)
        catalog = list_all_cues(cfg.project_root, cues_config=cfg.cues)
        catalog["resolve_examples"] = {
            ref: describe_resolution(
                ref, project_root=cfg.project_root, cues_config=cfg.cues
            )
            for ref in ("builtin:noise.loud", "builtin:noise.ambient", "@backchannel")
        }
        return catalog
    except ConfigError:
        # Still list package built-ins without target config
        return list_all_cues(root if (root / DOT_FOLDER).is_dir() else None)


async def _run_scenario_dict(project_root: Path | str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Internal: run dict after preflight (no schema validation wrapper)."""
    cfg = load_config(project_root)
    pf = await preflight(cfg.project_root, connectivity=True)
    if not pf["ok"]:
        failed = [c for c in pf["checks"] if c["status"] == "fail"]
        raise RuntimeError("Preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))
    scenario_id = str(scenario.get("id") or (scenario.get("metadata") or {}).get("id", "dynamic"))
    s = scenario_from_dict(scenario, path=cfg.scenarios_dir / f"{scenario_id}.jsonl")
    return await run_orchestrator.run_scenario_instance(cfg, s)


async def _run_scenario(project_root: Path | str, scenario_id: str) -> dict[str, Any]:
    """Internal: run JSONL scenario after preflight (orchestrator also preflights)."""
    cfg = load_config(project_root)
    return await run_orchestrator.run_scenario(cfg, scenario_id)


async def execute_scenario_dict(project_root: Path | str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Validate dict-shaped scenario then run (no JSONL file on disk required)."""
    try:
        scenario_from_dict(scenario)
    except ScenarioError as e:
        return {"executed": False, "validation": {"valid": False, "error": str(e)}}
    result = await _run_scenario_dict(project_root, scenario)
    return {"executed": True, "validation": {"valid": True}, **result}


async def execute_scenario(
    project_root: Path | str,
    scenario_id: str,
    *,
    repeat: int = 1,
    pass_at_k: int | None = None,
) -> dict[str, Any]:
    """Validate then run one scenario from `.agent-sim/scenarios/<id>.jsonl`.

    ``repeat`` / ``pass_at_k`` enable flake-tolerant execution (pass@k).
    ``repeat=1`` (default) preserves single-shot semantics.
    """
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")
    k = pass_at_k if pass_at_k is not None else repeat
    if k > repeat:
        raise ValueError(f"pass_at_k ({k}) cannot exceed repeat ({repeat})")

    validation = validate_scenario(project_root, scenario_id)
    if not validation.get("valid"):
        return {"executed": False, "validation": validation}

    from .suite import evaluate_run_result
    from .metrics import metrics_digest

    iterations: list[dict[str, Any]] = []
    hard_passes = 0

    for i in range(repeat):
        try:
            result = await _run_scenario(project_root, scenario_id)
            # _run_scenario returns raw orchestrator result without executed/validation
            result["executed"] = True
            result["validation"] = {"valid": True, "id": scenario_id}
        except Exception as e:
            result = {"executed": True, "run_id": None, "status": "failed", "error": f"{type(e).__name__}: {e}"}
        gate = evaluate_run_result(result)
        summary = result.get("summary") or {}
        mdig = metrics_digest(summary.get("metrics") if isinstance(summary.get("metrics"), dict) else None)
        iterations.append({
            "i": i + 1,
            "run_id": result.get("run_id") or summary.get("run_id"),
            "status": result.get("status"),
            "gate": gate["gate"],
            "ok": gate["ok"],
            "hard_reasons": gate["hard_reasons"],
            "ttfw_ms": mdig.get("ttfw_ms"),
            "turn_p50_ms": mdig.get("turn_p50_ms"),
            "turn_p95_ms": mdig.get("turn_p95_ms"),
        })
        if gate["ok"]:
            hard_passes += 1

    ok = hard_passes >= k
    out: dict[str, Any] = {
        "executed": True,
        "validation": validation,
        "repeat": repeat,
        "pass_at_k": k,
        "hard_passes": hard_passes,
        "ok": ok,
        "iterations": iterations,
    }
    # Attach last result fields for backward compat
    if iterations:
        last = iterations[-1]
        out["run_id"] = last["run_id"]
        out["status"] = last["status"]
        out["iterations"] = iterations
        # Re-run summary from last non-failed iter if possible
        for it in reversed(iterations):
            if it["run_id"]:
                try:
                    from .logging.sqlite_store import RunStore
                    cfg = load_config(project_root)
                    run = await RunStore(cfg.sqlite_path).get_run(it["run_id"])
                    if run and run.get("status") == "done":
                        report_dir = cfg.reports_dir / it["run_id"]
                        summary_path = report_dir / "summary.json"
                        if summary_path.exists():
                            import json
                            out["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
                            break
                except Exception:
                    pass
    return out


async def execute_scenarios(
    project_root: Path | str,
    scenario_ids: list[str] | None = None,
    tag: str | None = None,
    *,
    strict_judge: bool = False,
    write_report: bool = True,
    repeat: int = 1,
    pass_at_k: int | None = None,
    parallel: int = 1,
) -> dict[str, Any]:
    """Run multiple scenarios + suite matrix / CI gate.

    Hard gate (``suite.ok`` / exit): status, assert_verify, script_verify.
    Judge fail is soft unless ``strict_judge=True``.

    ``repeat`` / ``pass_at_k`` propagate to each scenario (pass@k).
    ``parallel`` runs up to N scenarios concurrently (default 1 = sequential).
    Within a scenario, ``repeat`` iterations stay sequential.
    """
    import asyncio

    from .suite import build_suite_report, write_suite_report

    if parallel < 1:
        raise ValueError(f"parallel must be >= 1, got {parallel}")

    cfg = load_config(project_root)
    listed = _list_scenarios(cfg.scenarios_dir)
    if scenario_ids:
        targets = scenario_ids
    else:
        targets = [
            item["id"]
            for item in listed
            if item.get("id") and not item.get("error") and (not tag or tag in (item.get("tags") or []))
        ]

    async def _one(sid: str) -> dict[str, Any]:
        try:
            return await execute_scenario(
                project_root, sid, repeat=repeat, pass_at_k=pass_at_k
            )
        except Exception as e:
            return {
                "executed": False,
                "scenario_id": sid,
                "error": f"{type(e).__name__}: {e}",
            }

    if parallel == 1 or len(targets) <= 1:
        results: list[dict[str, Any]] = []
        for sid in targets:
            results.append(await _one(sid))
    else:
        sem = asyncio.Semaphore(parallel)

        async def _bounded(sid: str) -> dict[str, Any]:
            async with sem:
                return await _one(sid)

        # Preserve input order in the suite matrix
        results = list(await asyncio.gather(*[_bounded(sid) for sid in targets]))

    suite = build_suite_report(results, strict_judge=strict_judge, tag=tag)
    out: dict[str, Any] = {
        "count": len(results),
        "results": results,
        "suite": suite,
        "ok": suite["ok"],
        "exit_code": suite["exit_code"],
        "parallel": parallel,
    }
    if write_report:
        paths = write_suite_report(suite, cfg.reports_dir)
        out["suite_report"] = paths
    return out


async def get_run_status(project_root: Path | str, run_id: str) -> dict[str, Any]:
    cfg = load_config(project_root)
    run = await RunStore(cfg.sqlite_path).get_run(run_id)
    if run is None:
        return {"found": False, "run_id": run_id}
    return {
        "found": True,
        "run_id": run["run_id"],
        "status": run["status"],
        "scenario_id": run["scenario_id"],
        "room_name": run["room_name"],
        "started_utc": run["started_utc"],
        "ended_utc": run["ended_utc"],
        "duration_ms": run["duration_ms"],
        "turn_count": run["turn_count"],
        "tool_errors": run["tool_errors"],
        "report_dir": run["report_dir"],
    }


def get_run_log(
    project_root: Path | str,
    run_id: str,
    kind: str | None = None,
    turn: int | None = None,
    source: str | None = None,
    since_mono_ms: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Read events.jsonl with filters. `kind` supports prefix match with trailing `*`."""
    cfg = load_config(project_root)
    events_path = cfg.reports_dir / run_id / "events.jsonl"
    if not events_path.exists():
        return {"found": False, "run_id": run_id, "error": f"{events_path} not found"}

    out: list[dict[str, Any]] = []
    total = 0
    with events_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            total += 1
            if kind:
                if kind.endswith("*"):
                    if not e["kind"].startswith(kind[:-1]):
                        continue
                elif e["kind"] != kind:
                    continue
            if turn is not None and e.get("turn") != turn:
                continue
            if source and e.get("source") != source:
                continue
            if since_mono_ms is not None and e.get("ts_mono_ms", 0) < since_mono_ms:
                continue
            out.append(e)

    truncated = len(out) > limit
    return {
        "found": True,
        "run_id": run_id,
        "total_events": total,
        "matched": len(out),
        "truncated": truncated,
        "events": out[:limit],
    }


async def get_run_report(project_root: Path | str, run_id: str) -> dict[str, Any]:
    cfg = load_config(project_root)
    report_dir = cfg.reports_dir / run_id
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        status = await get_run_status(project_root, run_id)
        return {"found": False, "run_id": run_id, "status": status}

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    meta_path = report_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    suspicious: list[dict[str, Any]] = []
    warn_ms = cfg.observe.turn_taking_warn_ms
    for t in summary.get("turns", []):
        reasons = []
        if t.get("tool_errors"):
            reasons.append(f"{t['tool_errors']} tool error(s)")
        if t.get("turn_taking_ms") is not None and t["turn_taking_ms"] > warn_ms:
            reasons.append(f"slow turn-taking {t['turn_taking_ms']}ms > {warn_ms}ms")
        if t.get("interrupted"):
            reasons.append("interrupted")
        if reasons:
            suspicious.append({**t, "reasons": reasons})

    audio_path = report_dir / "conversation.wav"
    return {
        "found": True,
        "run_id": run_id,
        "summary": summary,
        "meta": meta,
        "suspicious_turns": suspicious,
        "timeline_path": str(report_dir / "timeline.md"),
        "events_path": str(report_dir / "events.jsonl"),
        "audio_path": str(audio_path) if audio_path.exists() else None,
    }


async def compare_runs(project_root: Path | str, run_id_a: str, run_id_b: str) -> dict[str, Any]:
    a = await get_run_report(project_root, run_id_a)
    b = await get_run_report(project_root, run_id_b)
    if not a.get("found") or not b.get("found"):
        return {"error": "one or both runs not found", "a": a.get("found"), "b": b.get("found")}

    def digest(r: dict[str, Any]) -> dict[str, Any]:
        from .metrics import metrics_digest

        s = r["summary"]
        md = metrics_digest(s.get("metrics") if isinstance(s.get("metrics"), dict) else None)
        return {
            "run_id": r["run_id"],
            "status": s.get("status"),
            "duration_ms": s.get("duration_ms"),
            "turn_count": s.get("turn_count"),
            "tool_errors": s.get("tool_errors"),
            "interruptions": s.get("interruptions"),
            "turn_taking_p50": md.get("turn_p50_ms")
            or (s.get("turn_taking_ms") or {}).get("p50"),
            "turn_taking_p95": md.get("turn_p95_ms")
            or (s.get("turn_taking_ms") or {}).get("p95"),
            "ttfw_ms": md.get("ttfw_ms"),
            "recovery_p50_ms": md.get("recovery_p50_ms"),
            "barge_count": md.get("barge_count"),
            "barge_recovery_rate": md.get("barge_recovery_rate"),
            "talk_ratio": md.get("talk_ratio"),
            "verdict": (s.get("verdict") or {}).get("verdict"),
        }

    da, db = digest(a), digest(b)
    deltas = {
        k: {"a": da[k], "b": db[k]}
        for k in da
        if k != "run_id" and da[k] != db[k]
    }
    return {"a": da, "b": db, "deltas": deltas}


async def list_runs(
    project_root: Path | str, limit: int = 20, scenario_id: str | None = None
) -> list[dict[str, Any]]:
    cfg = load_config(project_root)
    if not cfg.sqlite_path.exists():
        return []
    return await RunStore(cfg.sqlite_path).list_runs(limit=limit, scenario_id=scenario_id)


def scenario_from_run(
    project_root: Path | str,
    run_id: str,
    *,
    scenario_id: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Promote a finished run into a draft scenario JSONL (fail → golden).

    Reads ``reports/<run_id>/`` and synthesizes an agent-sim/v1 draft.
    Dry-run by default (prints to stdout); use ``write=True`` to write
    ``.agent-sim/scenarios/<id>.jsonl``.

    Result keys: ``scenario_id``, ``source_run_id``, ``jsonl`` (str),
    ``warnings`` (list), ``kinds``, ``latency_hint``, ``stats``.
    """
    from .scenario_from_run import build_scenario_draft_from_run

    cfg = load_config(project_root)
    report_dir = cfg.reports_dir / run_id
    if not report_dir.is_dir():
        raise ConfigError(f"Run report dir not found: {report_dir}")

    draft = build_scenario_draft_from_run(
        report_dir,
        scenario_id=scenario_id,
        locale_default=cfg.simulator.language,
    )

    if write:
        dest = cfg.scenarios_dir / f"{draft['scenario_id']}.jsonl"
        dest.write_text(draft["jsonl"], encoding="utf-8")
        draft["written_to"] = str(dest)
    return draft


def web(
    project_root: Path | str,
    run_id: str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    blocking: bool = True,
) -> dict[str, Any]:
    """Serve local report player (audio + transcript sync). Blocks until Ctrl+C if blocking."""
    from .web.server import start_web_server

    cfg = load_config(project_root)
    reports = cfg.reports_dir
    if not reports.is_dir():
        raise ConfigError(f"No reports dir yet: {reports} — run execute first")
    info = start_web_server(
        reports,
        host=host,
        port=port,
        open_browser=open_browser,
        run_id=run_id,
        blocking=blocking,
    )
    # Drop non-JSON objects (HTTP server handles) for CLI/MCP responses.
    return {k: v for k, v in info.items() if k not in ("server", "thread")}


__all__ = [
    "ConfigError",
    "init_project",
    "preflight",
    "guide",
    "web",
    "init_scenario",
    "list_scenarios",
    "list_plugins",
    "validate_scenario",
    "export_scenario",
    "execute_scenario",
    "execute_scenarios",
    "execute_scenario_dict",
    "scenario_from_run",
    "get_run_status",
    "get_run_log",
    "get_run_report",
    "compare_runs",
    "list_runs",
]
