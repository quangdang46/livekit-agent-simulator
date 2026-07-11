"""Local report web UI (audio + transcript sync during playback)."""

from .cues import build_cues_payload, write_cues_json
from .server import start_web_server

__all__ = ["build_cues_payload", "write_cues_json", "start_web_server"]
