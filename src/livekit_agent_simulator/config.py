"""Load and validate `.agent-sim/config.yaml`.

POC contract (see plan): credentials are written directly in the file; the whole
`.agent-sim/` folder is gitignored. No env-var substitution in v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOT_FOLDER = ".agent-sim"
CONFIG_FILENAME = "config.yaml"

# Portable defaults — demos may override in target config / templates.
DEFAULT_LANGUAGE = "en-US"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_VOICE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"


class ConfigError(Exception):
    """Raised when config.yaml is missing or invalid. Message is user-actionable."""


@dataclass
class LiveKitConfig:
    url: str
    api_key: str
    api_secret: str
    agent_name: str
    room_prepare_ms: int = 500
    agent_join_timeout_ms: int = 25_000
    dispatch_metadata: str | None = None


@dataclass
class SimulatorVoiceConfig:
    """Gemini Live voice for the simulated caller."""

    model: str = DEFAULT_VOICE_MODEL
    voice: str = "Puck"
    language: str = DEFAULT_LANGUAGE


@dataclass
class SimulatorConfig:
    google_api_key: str
    language: str = DEFAULT_LANGUAGE
    voice: SimulatorVoiceConfig = field(default_factory=SimulatorVoiceConfig)


@dataclass
class JudgeConfig:
    model: str = DEFAULT_JUDGE_MODEL
    temperature: float = 0.0


@dataclass
class ToolEventPattern:
    match: dict[str, Any]
    emit: str  # tool.start | tool.end | tool.error


@dataclass
class ObserveConfig:
    timezone: str = DEFAULT_TIMEZONE
    lk_transcription: bool = True
    lk_agent_session: bool = True
    # Local-first stereo WAV under reports/<run-id>/conversation.wav (no Egress).
    # L = sim caller, R = agent.
    record_audio: bool = False
    data_topics: list[str] = field(default_factory=list)
    tool_event_patterns: list[ToolEventPattern] = field(default_factory=list)
    # Sim wire format: payload `type` values treated as transcript turns on data topics.
    transcript_payload_types: list[str] = field(default_factory=lambda: ["transcript_turn"])
    transcript_dedupe_window_ms: int = 15_000
    silence_threshold_ms: int = 4_000
    turn_taking_warn_ms: int = 2_500

    @property
    def audio_recording_enabled(self) -> bool:
        return bool(self.record_audio)


@dataclass
class CuesConfig:
    """Per-target room_pcm library: extra dirs + name aliases (see cue_catalog)."""

    dirs: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class SimConfig:
    project_root: Path
    livekit: LiveKitConfig
    simulator: SimulatorConfig
    observe: ObserveConfig = field(default_factory=ObserveConfig)
    judge: JudgeConfig | None = None
    project: str | None = None
    cues: CuesConfig = field(default_factory=CuesConfig)

    @property
    def dot_dir(self) -> Path:
        return self.project_root / DOT_FOLDER

    @property
    def reports_dir(self) -> Path:
        return self.dot_dir / "reports"

    @property
    def scenarios_dir(self) -> Path:
        return self.dot_dir / "scenarios"

    @property
    def cues_dir(self) -> Path:
        """Target override library: ``.agent-sim/cues/*.wav``."""
        return self.dot_dir / "cues"

    @property
    def sqlite_path(self) -> Path:
        return self.dot_dir / "runs.sqlite"


def _require(section: dict[str, Any], key: str, section_name: str) -> Any:
    value = section.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ConfigError(
            f"Missing `{section_name}.{key}` in {DOT_FOLDER}/{CONFIG_FILENAME}. "
            f"Copy the value from LiveKit Cloud / your worker and try again."
        )
    return value


def load_config(project_root: Path | str) -> SimConfig:
    project_root = Path(project_root).resolve()
    config_path = project_root / DOT_FOLDER / CONFIG_FILENAME
    if not config_path.exists():
        raise ConfigError(
            f"{config_path} not found. Run `lk-sim init` (or the `init_project` MCP tool) "
            f"to scaffold {DOT_FOLDER}/ first."
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{config_path} is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must be a YAML mapping at the top level.")

    lk_raw = raw.get("livekit")
    if not isinstance(lk_raw, dict):
        raise ConfigError(f"Missing `livekit:` section in {config_path}.")
    dispatch_metadata = lk_raw.get("dispatch_metadata")
    if dispatch_metadata is not None:
        dispatch_metadata = str(dispatch_metadata).strip() or None

    livekit = LiveKitConfig(
        url=str(_require(lk_raw, "url", "livekit")),
        api_key=str(_require(lk_raw, "api_key", "livekit")),
        api_secret=str(_require(lk_raw, "api_secret", "livekit")),
        agent_name=str(_require(lk_raw, "agent_name", "livekit")),
        room_prepare_ms=int(lk_raw.get("room_prepare_ms", 500)),
        agent_join_timeout_ms=int(lk_raw.get("agent_join_timeout_ms", 25_000)),
        dispatch_metadata=dispatch_metadata,
    )

    sim_raw = raw.get("simulator")
    if not isinstance(sim_raw, dict):
        raise ConfigError(f"Missing `simulator:` section in {config_path}.")
    voice_raw = sim_raw.get("voice") or {}
    default_lang = str(sim_raw.get("language", DEFAULT_LANGUAGE))
    voice = SimulatorVoiceConfig(
        model=str(voice_raw.get("model", DEFAULT_VOICE_MODEL)),
        voice=str(voice_raw.get("voice", "Puck")),
        language=str(voice_raw.get("language", default_lang)),
    )
    simulator = SimulatorConfig(
        google_api_key=str(_require(sim_raw, "google_api_key", "simulator")),
        language=default_lang,
        voice=voice,
    )

    judge: JudgeConfig | None = None
    judge_raw = raw.get("judge")
    if isinstance(judge_raw, dict):
        judge = JudgeConfig(
            model=str(judge_raw.get("model", DEFAULT_JUDGE_MODEL)),
            temperature=float(judge_raw.get("temperature", 0.0)),
        )

    obs_raw = raw.get("observe") or {}
    patterns: list[ToolEventPattern] = []
    for p in obs_raw.get("tool_event_patterns") or []:
        if isinstance(p, dict) and isinstance(p.get("match"), dict) and p.get("emit"):
            patterns.append(ToolEventPattern(match=p["match"], emit=str(p["emit"])))

    observe = ObserveConfig(
        timezone=str(obs_raw.get("timezone", DEFAULT_TIMEZONE)),
        lk_transcription=bool(obs_raw.get("lk_transcription", True)),
        lk_agent_session=bool(obs_raw.get("lk_agent_session", True)),
        record_audio=bool(obs_raw.get("record_audio", False)),
        data_topics=[str(t) for t in (obs_raw.get("data_topics") or [])],
        tool_event_patterns=patterns,
        transcript_payload_types=[
            str(t) for t in (obs_raw.get("transcript_payload_types") or ["transcript_turn"])
        ],
        transcript_dedupe_window_ms=int(obs_raw.get("transcript_dedupe_window_ms", 15_000)),
        silence_threshold_ms=int(obs_raw.get("silence_threshold_ms", 4_000)),
        turn_taking_warn_ms=int(obs_raw.get("turn_taking_warn_ms", 2_500)),
    )

    cues = CuesConfig()
    cues_raw = raw.get("cues")
    if isinstance(cues_raw, dict):
        dirs_raw = cues_raw.get("dirs") or []
        if not isinstance(dirs_raw, list):
            raise ConfigError("`cues.dirs` must be a list of directory paths.")
        aliases_raw = cues_raw.get("aliases") or {}
        if not isinstance(aliases_raw, dict):
            raise ConfigError("`cues.aliases` must be a mapping of name → path/asset.")
        cues = CuesConfig(
            dirs=[str(d) for d in dirs_raw],
            aliases={str(k): str(v) for k, v in aliases_raw.items()},
        )

    return SimConfig(
        project_root=project_root,
        livekit=livekit,
        simulator=simulator,
        observe=observe,
        judge=judge,
        project=raw.get("project"),
        cues=cues,
    )


def config_snapshot(cfg: SimConfig) -> dict[str, Any]:
    """Redacted config for `run.started.config_snapshot` — never includes secrets."""
    gaps: list[str] = []
    if not cfg.observe.lk_agent_session and not cfg.observe.tool_event_patterns:
        gaps.append("tool_events")
    return {
        "project": cfg.project,
        "livekit": {
            "url_host": cfg.livekit.url.split("://")[-1].split("/")[0],
            "agent_name": cfg.livekit.agent_name,
            "agent_join_timeout_ms": cfg.livekit.agent_join_timeout_ms,
            "dispatch_metadata_set": bool(cfg.livekit.dispatch_metadata),
        },
        "simulator": {
            "voice_model": cfg.simulator.voice.model,
            "voice": cfg.simulator.voice.voice,
            "language": cfg.simulator.voice.language,
        },
        "judge_enabled": cfg.judge is not None,
        "cues": {
            "dirs": list(cfg.cues.dirs),
            "alias_keys": sorted(cfg.cues.aliases.keys()),
            "target_cues_dir": str(cfg.cues_dir),
        },
        "observe": {
            "lk_transcription": cfg.observe.lk_transcription,
            "lk_agent_session": cfg.observe.lk_agent_session,
            "record_audio": cfg.observe.audio_recording_enabled,
            "data_topics": cfg.observe.data_topics,
            "silence_threshold_ms": cfg.observe.silence_threshold_ms,
        },
        "observe_gaps": gaps,
    }
