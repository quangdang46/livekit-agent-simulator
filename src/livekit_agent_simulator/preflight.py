"""Mandatory preflight checks before any run — fail fast with actionable messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError, SimConfig, load_config


@dataclass
class PreflightResult:
    ok: bool
    checks: list[dict[str, str]] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})
        if status == "fail":
            self.ok = False


async def run_preflight(project_root: Path | str, connectivity: bool = True) -> tuple[PreflightResult, SimConfig | None]:
    """Validate config, timezone, folders, and (optionally) LiveKit API reachability."""
    result = PreflightResult(ok=True)

    try:
        cfg = load_config(project_root)
        result.add("config", "pass", str(cfg.dot_dir / "config.yaml"))
    except ConfigError as e:
        result.add("config", "fail", str(e))
        return result, None

    if not cfg.livekit.url.startswith(("ws://", "wss://", "http://", "https://")):
        result.add("livekit.url", "fail", f"`{cfg.livekit.url}` must start with wss:// (LiveKit Cloud) or ws://")
    else:
        result.add("livekit.url", "pass", cfg.livekit.url)

    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(cfg.observe.timezone)
        result.add("observe.timezone", "pass", cfg.observe.timezone)
    except Exception:
        result.add("observe.timezone", "fail", f"Unknown IANA timezone `{cfg.observe.timezone}`")

    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    cfg.scenarios_dir.mkdir(parents=True, exist_ok=True)
    result.add("folders", "pass", str(cfg.dot_dir))

    key = cfg.simulator.google_api_key.strip()
    if len(key) < 20:
        result.add("simulator.google_api_key", "warn", "Key looks unusually short")
    else:
        result.add("simulator.google_api_key", "pass", "present")

    if connectivity and result.ok:
        await _check_livekit_api(cfg, result)

    # Optional telephony surface (informational unless required by a SIP scenario at run time).
    tel = cfg.telephony
    if tel.outbound_trunk_id or tel.dial_in or tel.sim_inbound_number:
        bits = []
        bits.append("outbound_trunk=" + ("set" if tel.outbound_trunk_id else "missing"))
        bits.append("dial_in=" + ("set" if tel.dial_in else "unset"))
        bits.append("sim_inbound=" + ("set" if tel.sim_inbound_number else "unset"))
        result.add("telephony", "pass" if tel.outbound_trunk_id else "warn", "; ".join(bits))
    else:
        result.add("telephony", "pass", "not configured (WebRTC-only OK)")

    return result, cfg


async def _check_livekit_api(cfg: SimConfig, result: PreflightResult) -> None:
    """List rooms with the given credentials — proves URL + key + secret are valid."""
    try:
        from livekit import api

        lkapi = api.LiveKitAPI(cfg.livekit.url, cfg.livekit.api_key, cfg.livekit.api_secret)
        try:
            await lkapi.room.list_rooms(api.ListRoomsRequest())
            result.add("livekit.api", "pass", "list_rooms OK")
        finally:
            await lkapi.aclose()
    except Exception as e:
        result.add(
            "livekit.api",
            "fail",
            f"Cannot reach LiveKit server API with the configured credentials: {type(e).__name__}: {e}",
        )
