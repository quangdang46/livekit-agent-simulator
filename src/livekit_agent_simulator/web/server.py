"""Local HTTP server for report playback UI."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..paths import package_templates_dir
from .cues import build_cues_payload, write_cues_json

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _player_dir() -> Path:
    return package_templates_dir() / "report-player"


def list_run_ids(reports_dir: Path) -> list[str]:
    if not reports_dir.is_dir():
        return []
    runs = [
        p.name
        for p in reports_dir.iterdir()
        if p.is_dir() and (p / "events.jsonl").exists()
    ]
    return sorted(runs, reverse=True)


class ReportUIHandler(SimpleHTTPRequestHandler):
    """Serves player assets + per-run reports under /runs/<id>/."""

    # Set by factory
    reports_dir: Path
    player_dir: Path

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quieter default; still useful when debugging
        if "404" in (fmt % args):
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._serve_file(self.player_dir / "index.html", "text/html; charset=utf-8")
        if path == "/player.html":
            return self._serve_file(self.player_dir / "player.html", "text/html; charset=utf-8")
        if path == "/player.js":
            return self._serve_file(self.player_dir / "player.js", "application/javascript; charset=utf-8")
        if path == "/player.css":
            return self._serve_file(self.player_dir / "player.css", "text/css; charset=utf-8")

        if path == "/api/runs":
            runs = []
            for rid in list_run_ids(self.reports_dir):
                rd = self.reports_dir / rid
                summary = {}
                sp = rd / "summary.json"
                if sp.exists():
                    try:
                        summary = json.loads(sp.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        summary = {}
                runs.append(
                    {
                        "run_id": rid,
                        "status": summary.get("status"),
                        "duration_ms": summary.get("duration_ms"),
                        "turn_count": summary.get("turn_count"),
                        "has_audio": (rd / "conversation.wav").exists(),
                    }
                )
            return self._json(runs)

        if path.startswith("/api/runs/"):
            rest = path[len("/api/runs/") :].strip("/")
            parts = rest.split("/")
            run_id = parts[0] if parts else ""
            report_dir = self.reports_dir / run_id
            if not run_id or not report_dir.is_dir():
                return self._error(404, "run not found")
            if len(parts) == 1 or parts[1] == "cues":
                payload = build_cues_payload(report_dir)
                write_cues_json(report_dir)
                return self._json(payload)
            return self._error(404, "unknown api path")

        if path.startswith("/runs/"):
            # /runs/<run_id>/conversation.wav | cues.json | ...
            rel = path[len("/runs/") :]
            parts = rel.split("/", 1)
            run_id = parts[0]
            name = parts[1] if len(parts) > 1 else ""
            report_dir = (self.reports_dir / run_id).resolve()
            if not str(report_dir).startswith(str(self.reports_dir.resolve())):
                return self._error(403, "forbidden")
            if not report_dir.is_dir():
                return self._error(404, "run not found")
            if not name or name in ("", "player"):
                # Serve player with ?run= in query — redirect
                qs = parse_qs(parsed.query)
                if "run" not in qs:
                    return self._redirect(f"/player.html?run={run_id}")
                return self._serve_file(self.player_dir / "player.html", "text/html; charset=utf-8")
            if name == "cues.json":
                write_cues_json(report_dir)
                return self._serve_file(report_dir / "cues.json", "application/json; charset=utf-8")
            # Safe file under report dir
            target = (report_dir / name).resolve()
            if not str(target).startswith(str(report_dir)):
                return self._error(403, "forbidden")
            if not target.is_file():
                return self._error(404, "file not found")
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self._serve_file(target, ctype)

        return self._error(404, "not found")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _error(self, code: int, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            return self._error(404, f"missing {path.name}")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if content_type.startswith("audio/"):
            self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(data)


def start_web_server(
    reports_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    run_id: str | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Start report UI server. Returns {url, host, port, run_id, runs}."""
    reports_dir = Path(reports_dir).resolve()
    player_dir = _player_dir()
    if not (player_dir / "player.html").exists():
        raise FileNotFoundError(f"Report player assets missing: {player_dir}")

    runs = list_run_ids(reports_dir)
    if run_id is None and runs:
        run_id = runs[0]
    if run_id:
        rd = reports_dir / run_id
        if rd.is_dir():
            write_cues_json(rd)

    ReportUIHandler.reports_dir = reports_dir
    ReportUIHandler.player_dir = player_dir

    httpd = ThreadingHTTPServer((host, port), ReportUIHandler)
    base = f"http://{host}:{port}"
    path = f"/player.html?run={run_id}" if run_id else "/"
    url = base + path

    thread = threading.Thread(target=httpd.serve_forever, name="lk-sim-web", daemon=True)
    thread.start()

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    info = {
        "url": url,
        "base_url": base,
        "host": host,
        "port": port,
        "run_id": run_id,
        "runs": runs,
        "reports_dir": str(reports_dir),
    }

    if blocking:
        try:
            thread.join()
        except KeyboardInterrupt:
            httpd.shutdown()
    else:
        info["server"] = httpd  # caller may shutdown
        info["thread"] = thread

    return info
