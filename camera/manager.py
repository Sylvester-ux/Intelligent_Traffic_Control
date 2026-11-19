"""Lifecycle and health aggregation for every configured camera."""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from camera.base import Frame, FrameSource, SourceHealth, SourceState

_log = logging.getLogger(__name__)


class CameraManager:
    """Starts all sources, exposes latest frames and per-camera health."""

    def __init__(self, sources: Mapping[str, FrameSource]) -> None:
        self._sources: dict[str, FrameSource] = dict(sources)

    @property
    def camera_ids(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def start_all(self) -> None:
        for camera_id, source in self._sources.items():
            _log.info("starting camera %s", camera_id)
            source.start()

    def stop_all(self) -> None:
        for camera_id, source in self._sources.items():
            _log.info("stopping camera %s", camera_id)
            try:
                source.stop()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                _log.warning("error stopping camera %s", camera_id, exc_info=True)

    def get_latest(self, camera_id: str) -> Optional[Frame]:
        return self._sources[camera_id].get_latest()

    def health(self, camera_id: str) -> SourceHealth:
        return self._sources[camera_id].health()

    def health_all(self) -> dict[str, SourceHealth]:
        return {camera_id: source.health() for camera_id, source in self._sources.items()}

    def is_live(self, camera_id: str, max_age_s: float = 2.0) -> bool:
        health = self.health(camera_id)
        if health.state is not SourceState.LIVE:
            return False
        age = health.last_frame_age_s
        return age is not None and age <= max_age_s

    def live_camera_ids(self, max_age_s: float = 2.0) -> set[str]:
        return {
            camera_id
            for camera_id in self._sources
            if self.is_live(camera_id, max_age_s=max_age_s)
        }

    def __enter__(self) -> "CameraManager":
        self.start_all()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop_all()
