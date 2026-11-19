"""Typed configuration loaded from YAML.

Only ``config.example.yaml`` is committed. The real ``config.yaml`` is ignored
because it holds RTSP credentials (or references them through ``${VAR}``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from camera.base import FrameSource
from camera.file_source import FileSource
from camera.rtsp import RtspSource
from camera.webcam import WebcamSource
from decision.models import WIRE_MOVEMENTS, ServingUnit
from decision.settings import DecisionSettings, Weights
from gateway.client import GatewayConfig
from pipeline.camera_pipeline import DEFAULT_POLYGON, PipelineConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised for malformed or inconsistent configuration."""


@dataclass(frozen=True)
class CameraConfig:
    id: str
    approach: str
    source: str
    url: Optional[str] = None
    index: int = 0
    path: Optional[str] = None
    flip: bool = False
    target_fps: float = 8.0
    polygon: tuple[tuple[float, float], ...] = DEFAULT_POLYGON
    count_line: float = 0.4
    width: Optional[int] = None
    height: Optional[int] = None
    stale_after_s: float = 2.0
    loop: bool = True
    realtime: bool = True
    movement_threshold: float = 5.0
    max_track_age_s: float = 5.0
    window_seconds: float = 10.0

    def pipeline_config(self) -> PipelineConfig:
        return PipelineConfig(
            polygon=self.polygon,
            count_line_fraction=self.count_line,
            movement_threshold=self.movement_threshold,
            target_fps=self.target_fps,
            stale_after_s=self.stale_after_s,
            max_track_age_s=self.max_track_age_s,
            window_seconds=self.window_seconds,
        )


@dataclass(frozen=True)
class DetectionConfig:
    model: str = "yolo11n.pt"
    device: str = "auto"
    confidence: float = 0.3
    imgsz: Optional[int] = None


@dataclass(frozen=True)
class HealthConfig:
    max_frame_age_s: float = 2.0
    tick_interval_s: float = 1.0
    tick_timeout_s: float = 2.0
    required_cameras: Optional[int] = None  # None = all configured cameras


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    decision_log: str = "logs/decisions.jsonl"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 3


@dataclass(frozen=True)
class AppConfig:
    cameras: tuple[CameraConfig, ...]
    units: tuple[ServingUnit, ...]
    decision: DecisionSettings
    gateway: GatewayConfig
    health: HealthConfig
    logging: LoggingConfig
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    display: bool = False

    @property
    def camera_ids(self) -> tuple[str, ...]:
        return tuple(camera.id for camera in self.cameras)

    @property
    def required_camera_count(self) -> int:
        if self.health.required_cameras is None:
            return len(self.cameras)
        return min(self.health.required_cameras, len(self.cameras))


# -- loading --------------------------------------------------------------


def load_config(path: str | Path, env: Optional[Mapping[str, str]] = None) -> AppConfig:
    """Load and validate a YAML configuration file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    return build_config(
        _expand_env(raw, env if env is not None else os.environ),
        base_dir=config_path.parent,
    )


def build_config(raw: Mapping[str, Any], *, base_dir: Optional[Path] = None) -> AppConfig:
    """Build a validated :class:`AppConfig` from already-expanded data."""
    base = Path(base_dir or ".")

    camera_entries = raw.get("cameras") or []
    if not isinstance(camera_entries, list) or not camera_entries:
        raise ConfigError("'cameras' must be a non-empty list")

    cameras: list[CameraConfig] = []
    for entry in camera_entries:
        if not isinstance(entry, dict):
            raise ConfigError("each camera entry must be a mapping")
        cameras.append(_camera_from(entry, base))

    ids = [camera.id for camera in cameras]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"camera ids must be unique, got {ids}")

    if "approaches" in raw:
        raise ConfigError(
            "'approaches' is no longer supported; assign approaches to serving "
            "units under decision.units"
        )
    if "phase_mode" in raw:
        raise ConfigError(
            "'phase_mode' was replaced by gateway.mode "
            "(compat_two_phase | three_phase)"
        )

    approach_owners: dict[str, str] = {}
    for camera in cameras:
        if camera.approach in approach_owners:
            raise ConfigError(
                f"approach {camera.approach!r} has two cameras "
                f"({approach_owners[camera.approach]!r} and {camera.id!r}); "
                "one camera per approach is supported"
            )
        approach_owners[camera.approach] = camera.id

    decision_raw = raw.get("decision") or {}
    if not isinstance(decision_raw, dict):
        raise ConfigError("'decision' must be a mapping")
    units = _units_from(decision_raw.get("units"))
    _validate_units(units, set(approach_owners))
    decision = _decision_settings(decision_raw)

    gateway_raw = raw.get("gateway") or {}
    if not isinstance(gateway_raw, dict):
        raise ConfigError("'gateway' must be a mapping")
    gateway = _build(GatewayConfig, gateway_raw, "gateway")

    health_raw = raw.get("health") or {}
    health = _build(HealthConfig, health_raw, "health")

    logging_raw = raw.get("logging") or {}
    logging_config = _build(LoggingConfig, logging_raw, "logging")

    detection_raw = raw.get("detection") or {}
    detection = _build(DetectionConfig, detection_raw, "detection")

    display = bool(raw.get("display", False))

    return AppConfig(
        cameras=tuple(cameras),
        units=units,
        decision=decision,
        gateway=gateway,
        health=health,
        logging=logging_config,
        detection=detection,
        display=display,
    )


def _camera_from(entry: Mapping[str, Any], base_dir: Path) -> CameraConfig:
    allowed = {
        "id",
        "approach",
        "source",
        "url",
        "index",
        "path",
        "flip",
        "target_fps",
        "polygon",
        "count_line",
        "width",
        "height",
        "stale_after_s",
        "loop",
        "realtime",
        "movement_threshold",
        "max_track_age_s",
        "window_seconds",
    }
    unknown = set(entry) - allowed
    if unknown:
        raise ConfigError(f"unknown camera keys: {sorted(unknown)}")

    camera_id = entry.get("id")
    source = entry.get("source")
    approach = entry.get("approach")
    if not camera_id or not isinstance(camera_id, str):
        raise ConfigError("each camera needs a string 'id'")
    if source not in ("rtsp", "webcam", "file"):
        raise ConfigError(
            f"camera {camera_id!r} has invalid source {source!r} "
            "(expected rtsp, webcam or file)"
        )
    if not approach or not isinstance(approach, str):
        raise ConfigError(f"camera {camera_id!r} needs an 'approach'")

    polygon = _polygon(entry.get("polygon"), camera_id)

    path = entry.get("path")
    if source == "file":
        if not path:
            raise ConfigError(f"camera {camera_id!r} is a file source without 'path'")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        path = str(candidate)

    url = entry.get("url")
    if source == "rtsp" and not url:
        raise ConfigError(f"camera {camera_id!r} is an rtsp source without 'url'")

    count_line = float(entry.get("count_line", 0.4))
    if not 0.0 <= count_line <= 1.0:
        raise ConfigError(f"camera {camera_id!r} count_line must be within [0, 1]")

    target_fps = float(entry.get("target_fps", 8.0))
    if target_fps <= 0:
        raise ConfigError(f"camera {camera_id!r} target_fps must be positive")

    return CameraConfig(
        id=camera_id,
        approach=approach,
        source=source,
        url=url,
        index=int(entry.get("index", 0)),
        path=path,
        flip=bool(entry.get("flip", False)),
        target_fps=target_fps,
        polygon=polygon,
        count_line=count_line,
        width=entry.get("width"),
        height=entry.get("height"),
        stale_after_s=float(entry.get("stale_after_s", 2.0)),
        loop=bool(entry.get("loop", True)),
        realtime=bool(entry.get("realtime", True)),
        movement_threshold=float(entry.get("movement_threshold", 5.0)),
        max_track_age_s=float(entry.get("max_track_age_s", 5.0)),
        window_seconds=float(entry.get("window_seconds", 10.0)),
    )


def _polygon(value: Any, camera_id: str) -> tuple[tuple[float, float], ...]:
    if value is None:
        return DEFAULT_POLYGON
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ConfigError(f"camera {camera_id!r} polygon needs at least 3 points")
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ConfigError(f"camera {camera_id!r} polygon points must be [x, y]")
        x, y = float(point[0]), float(point[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ConfigError(
                f"camera {camera_id!r} polygon coordinates must be normalized"
            )
        points.append((x, y))
    return tuple(points)


def _units_from(value: Any) -> tuple[ServingUnit, ...]:
    """Parse ``decision.units``: the engine's serving layout."""
    if not isinstance(value, list) or not value:
        raise ConfigError("decision.units must be a non-empty list")

    units: list[ServingUnit] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ConfigError("each decision.units entry must be a mapping")
        unknown = set(entry) - {"id", "approaches"}
        if unknown:
            raise ConfigError(
                f"unknown decision.units keys: {sorted(unknown)}"
            )
        unit_id = entry.get("id")
        approaches = entry.get("approaches")
        if not isinstance(unit_id, str) or not unit_id:
            raise ConfigError("each decision unit needs a non-empty string 'id'")
        if not isinstance(approaches, list) or not approaches:
            raise ConfigError(
                f"decision unit {unit_id!r} needs a non-empty 'approaches' list"
            )
        if not all(isinstance(item, str) and item for item in approaches):
            raise ConfigError(
                f"decision unit {unit_id!r} approaches must be non-empty strings"
            )
        units.append(ServingUnit(unit_id, tuple(approaches)))

    ids = [unit.id for unit in units]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"serving unit ids must be unique, got {ids}")

    assigned: dict[str, str] = {}
    for unit in units:
        for approach_id in unit.approaches:
            if approach_id in assigned:
                raise ConfigError(
                    f"approach {approach_id!r} is assigned to both "
                    f"{assigned[approach_id]!r} and {unit.id!r}"
                )
            assigned[approach_id] = unit.id

    return tuple(units)


def _validate_units(
    units: tuple[ServingUnit, ...], camera_approaches: set[str]
) -> None:
    """Every camera approach must be in exactly one unit, and vice versa."""
    unit_approaches = {
        approach_id for unit in units for approach_id in unit.approaches
    }
    missing = sorted(camera_approaches - unit_approaches)
    if missing:
        raise ConfigError(
            "camera approaches missing from decision.units: " + ", ".join(missing)
        )
    orphaned = sorted(unit_approaches - camera_approaches)
    if orphaned:
        raise ConfigError(
            "decision.units reference approaches with no camera: "
            + ", ".join(orphaned)
        )

    # This build speaks the current two-phase protocol: the unit id is the
    # wire movement until the protocol adapter lands.
    for unit in units:
        if unit.id not in WIRE_MOVEMENTS:
            raise ConfigError(
                f"serving unit {unit.id!r} is not a wire movement; this build "
                f"supports the two-phase layout {WIRE_MOVEMENTS}"
            )


def _decision_settings(raw: Mapping[str, Any]) -> DecisionSettings:
    allowed = {
        "weights",
        "base_green_s",
        "k_green_s",
        "score_saturation",
        "min_green_s",
        "max_green_s",
        "min_delta_s",
        "min_interval_s",
        "max_wait_before_force_s",
        "smoothing_window_s",
        "max_metrics_age_s",
        "idle_score_threshold",
        "aggregation",
        "require_all_approaches_healthy",
        "units",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"unknown decision keys: {sorted(unknown)}")

    weights_raw = raw.get("weights") or {}
    weights = _build(Weights, weights_raw, "decision.weights")

    aggregation = str(raw.get("aggregation", "sum"))
    if aggregation not in ("sum", "max"):
        raise ConfigError("decision.aggregation must be 'sum' or 'max'")

    kwargs: dict[str, Any] = {
        "weights": weights,
        "aggregation": aggregation,
    }
    for key in allowed - {"weights", "aggregation", "units"}:
        if key in raw:
            kwargs[key] = raw[key]

    try:
        return DecisionSettings(**kwargs)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid decision settings: {exc}") from exc


def _build(cls, data: Mapping[str, Any], section: str):
    if not isinstance(data, Mapping):
        raise ConfigError(f"'{section}' must be a mapping")
    names = set(getattr(cls, "__dataclass_fields__", {}))
    unknown = set(data) - names
    if unknown:
        raise ConfigError(f"unknown keys in '{section}': {sorted(unknown)}")
    try:
        return cls(**data)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid '{section}': {exc}") from exc


def _expand_env(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        missing: set[str] = set()

        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in env:
                missing.add(name)
                return match.group(0)
            return env[name]

        expanded = _ENV_PATTERN.sub(replace, value)
        if missing:
            raise ConfigError(
                "environment variable(s) not set: " + ", ".join(sorted(missing))
            )
        return expanded
    if isinstance(value, dict):
        return {key: _expand_env(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item, env) for item in value]
    return value


# -- runtime construction -------------------------------------------------


def build_source(camera: CameraConfig) -> FrameSource:
    """Create the frame source described by a camera config."""
    if camera.source == "rtsp":
        return RtspSource(
            camera.id,
            camera.url or "",
            flip=camera.flip,
            width=camera.width,
            height=camera.height,
            stale_after_s=camera.stale_after_s,
        )
    if camera.source == "webcam":
        return WebcamSource(
            camera.id,
            camera.index,
            flip=camera.flip,
            width=camera.width,
            height=camera.height,
            stale_after_s=camera.stale_after_s,
        )
    if camera.source == "file":
        return FileSource(
            camera.id,
            camera.path or "",
            loop=camera.loop,
            realtime=camera.realtime,
            flip=camera.flip,
            stale_after_s=camera.stale_after_s,
        )
    raise ConfigError(f"unsupported camera source {camera.source!r}")
