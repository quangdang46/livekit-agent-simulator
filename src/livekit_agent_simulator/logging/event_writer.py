"""Canonical event envelope + per-run report folder.

Every event carries: event_id, seq, run_id, turn, kind, ts (epoch ms), ts_mono_ms
(ms since run start), datetime_utc, datetime_local, source, parent_event_id,
dialogue snapshot (what user/agent said so far this turn), and a kind-specific spec.

Artifacts written under reports/<run-id>/:
    events.jsonl      append-only, one envelope per line (flushed on every emit)
    timeline.md       human-readable narrative table (written at finalize)
    summary.json      aggregates: duration, turns, metrics (TTFW/p50/p95/recovery), tool errors, verdict
    meta.json         run metadata: scenario, room, agent_name, config snapshot
    conversation.wav  local stereo PCM when observe.record_audio is true (L=sim, R=agent)
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round((pct / 100.0) * (len(values) - 1))))
    return values[idx]


class EventWriter:
    def __init__(
        self,
        run_id: str,
        report_dir: Path,
        timezone_name: str = "UTC",
        turn_taking_warn_ms: int = 2_500,
    ) -> None:
        self.run_id = run_id
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._tz = ZoneInfo(timezone_name)
        self._warn_ms = turn_taking_warn_ms

        self._seq = 0
        self._t0_mono = time.monotonic()
        self._events_path = self.report_dir / "events.jsonl"
        self._events_file = self._events_path.open("a", encoding="utf-8")
        self._events: list[dict[str, Any]] = []

        self.turn = 0
        self._dialogue: dict[str, dict[str, Any]] = {
            "user": {"text": None, "final": False, "at_ms": None},
            "agent": {"text": None, "final": False, "at_ms": None},
        }

    @property
    def t0_mono(self) -> float:
        """``time.monotonic()`` origin for ``ts_mono_ms`` (run start)."""
        return self._t0_mono

    # ---------------------------------------------------------------- dialogue

    def update_dialogue(self, role: str, text: str, final: bool, at_ms: int | None = None) -> None:
        """Keep the latest utterance per role. A new utterance after a final one replaces it."""
        assert role in ("user", "agent")
        self._dialogue[role] = {
            "text": text,
            "final": final,
            "at_ms": at_ms if at_ms is not None else int(time.time() * 1000),
        }

    def begin_turn(self, turn: int) -> None:
        """New turn: keep the user utterance that opened it, clear stale agent reply."""
        self.turn = turn
        self._dialogue["agent"] = {"text": None, "final": False, "at_ms": None}

    def dialogue_snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = {}
        for role in ("user", "agent"):
            d = dict(self._dialogue[role])
            if d["text"] is None:
                d["note"] = f"{role} has not spoken yet this turn"
            snap[role] = d
        return snap

    # -------------------------------------------------------------------- emit

    def emit(
        self,
        kind: str,
        spec: dict[str, Any] | None = None,
        source: str = "mcp",
        turn: int | None = None,
        parent_event_id: str | None = None,
        include_dialogue: bool = True,
    ) -> dict[str, Any]:
        self._seq += 1
        now = datetime.now(timezone.utc)
        event: dict[str, Any] = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "seq": self._seq,
            "run_id": self.run_id,
            "turn": self.turn if turn is None else turn,
            "kind": kind,
            "ts": int(now.timestamp() * 1000),
            "ts_mono_ms": int((time.monotonic() - self._t0_mono) * 1000),
            "datetime_utc": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "datetime_local": now.astimezone(self._tz).isoformat(timespec="milliseconds"),
            "source": source,
            "parent_event_id": parent_event_id,
        }
        if include_dialogue:
            event["dialogue"] = self.dialogue_snapshot()
        event["spec"] = spec or {}

        self._events.append(event)
        self._events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events_file.flush()
        return event

    # ---------------------------------------------------------------- finalize

    def finalize(
        self,
        status: str,
        meta: dict[str, Any] | None = None,
        verdict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compute metrics, emit run.ended, write timeline/summary/meta. Returns summary."""
        from ..metrics import compute_voice_metrics

        turns = self.turn_metrics()
        turn_taking = [t["turn_taking_ms"] for t in turns if t.get("turn_taking_ms") is not None]
        tool_errors = sum(1 for e in self._events if e["kind"] == "tool.error")
        tool_calls = sum(1 for e in self._events if e["kind"] == "tool.start")
        interruptions = sum(1 for e in self._events if e["kind"] == "interruption")
        silences = sum(1 for e in self._events if e["kind"] == "silence.detected")
        voice_metrics = compute_voice_metrics(self._events)
        tt_block = voice_metrics.get("turn_taking_ms") or {}

        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "status": status,
            "duration_ms": int((time.monotonic() - self._t0_mono) * 1000),
            "turn_count": max((e["turn"] for e in self._events), default=0),
            "event_count": self._seq + 1,  # + the run.ended emitted below
            # Backward-compatible top-level percentiles (full pack under metrics)
            "turn_taking_ms": {
                "p50": tt_block.get("p50", _percentile(turn_taking, 50)),
                "p95": tt_block.get("p95", _percentile(turn_taking, 95)),
                "p99": tt_block.get("p99"),
                "max": tt_block.get("max", max(turn_taking) if turn_taking else None),
                "count": tt_block.get("count", len(turn_taking)),
            },
            "metrics": voice_metrics,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "interruptions": interruptions,
            "silences": silences,
            "verdict": verdict,
            "turns": turns,
        }

        self.emit("run.ended", spec={"status": status, "summary_digest": {
            "turn_count": summary["turn_count"],
            "tool_errors": tool_errors,
            "duration_ms": summary["duration_ms"],
        }}, include_dialogue=False)

        (self.report_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if meta is not None:
            (self.report_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (self.report_dir / "timeline.md").write_text(self.render_timeline(), encoding="utf-8")

        self._events_file.close()
        return summary

    # ----------------------------------------------------------------- metrics

    def turn_metrics(self) -> list[dict[str, Any]]:
        """Aggregate per-turn rows from the event stream."""
        by_turn: dict[int, dict[str, Any]] = {}
        for e in self._events:
            t = e["turn"]
            if t <= 0:
                continue
            row = by_turn.setdefault(
                t,
                {
                    "turn": t,
                    "user_text": None,
                    "agent_text": None,
                    "turn_taking_ms": None,
                    "tool_count": 0,
                    "tool_errors": 0,
                    "interrupted": False,
                },
            )
            kind = e["kind"]
            spec = e.get("spec", {})
            if kind == "transcript.user.final":
                row["user_text"] = spec.get("text")
            elif kind == "transcript.agent.final":
                row["agent_text"] = spec.get("text")
                if spec.get("turn_taking_ms") is not None:
                    row["turn_taking_ms"] = spec["turn_taking_ms"]
            elif kind == "tool.start":
                row["tool_count"] += 1
            elif kind == "tool.error":
                row["tool_errors"] += 1
            elif kind == "interruption":
                row["interrupted"] = True
        return [by_turn[t] for t in sorted(by_turn)]

    # ---------------------------------------------------------------- timeline

    def render_timeline(self) -> str:
        lines = [
            f"# Timeline — {self.run_id}",
            "",
            "| local time | +ms | turn | kind | source | detail |",
            "|---|---|---|---|---|---|",
        ]
        for e in self._events:
            local_time = e["datetime_local"].split("T")[1][:12]
            detail = self._describe(e)
            warn = ""
            if e["kind"] == "transcript.agent.final":
                ttm = e.get("spec", {}).get("turn_taking_ms")
                if ttm is not None and ttm > self._warn_ms:
                    warn = f" ⚠ slow ({ttm}ms > {self._warn_ms}ms)"
            lines.append(
                f"| {local_time} | {e['ts_mono_ms']} | {e['turn']} | `{e['kind']}` | {e['source']} | {detail}{warn} |"
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _describe(e: dict[str, Any]) -> str:
        spec = e.get("spec", {})
        kind = e["kind"]
        if kind.startswith("transcript."):
            text = (spec.get("text") or "").replace("|", "\\|").replace("\n", " ")
            return text[:120]
        if kind.startswith("tool."):
            parts = [str(spec.get("name", "?"))]
            if spec.get("duration_ms") is not None:
                parts.append(f"{spec['duration_ms']}ms")
            if spec.get("error"):
                parts.append(f"error={spec['error']}")
            return " ".join(parts)
        if kind in ("session.agent_state", "session.user_state"):
            return f"{spec.get('old_state', '?')} → {spec.get('new_state', '?')}"
        if kind == "session.error":
            return str(spec.get("message") or spec.get("error") or "")[:120]
        if kind == "session.chat_history":
            return f"{len(spec.get('items') or [])} items"
        if kind == "session.usage":
            return f"{len(spec.get('model_usage') or [])} model usage entries"
        if kind == "silence.detected":
            return f"{spec.get('duration_ms', '?')}ms of silence"
        keys = [k for k in ("name", "identity", "topic", "status", "room", "node_id", "reason") if spec.get(k)]
        return ", ".join(f"{k}={spec[k]}" for k in keys)[:120]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._events

    @property
    def run_start_mono(self) -> float:
        return self._t0_mono
