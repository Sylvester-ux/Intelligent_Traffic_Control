"""One perception pipeline per camera.

A pipeline owns **all** state for its camera: detector/tracker, road region,
counter, congestion window and track history. Nothing is shared between
pipelines, which is what keeps track ids and counters from bleeding across
streams.

The worker thread always consumes the newest frame and skips frames instead of
queueing them, so a slow model degrades the frame rate rather than latency.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from analytics.congestion import CongestionAnalyzer
from analytics.road_region import RoadRegion
from analytics.vehicle_counter import VehicleCounter
from camera.base import Frame, FrameSource, SourceState
from decision.models import ApproachMetrics
from tracking.vehicle_tracker import VehicleTracker

_log = logging.getLogger(__name__)

DEFAULT_POLYGON: tuple[tuple[float, float], ...] = (
    (0.1, 0.3),
    (0.9, 0.3),
    (1.0, 0.95),
    (0.0, 0.95),
)


@dataclass(frozen=True)
class PipelineConfig:
    """Per-camera perception settings (all normalized where spatial)."""

    polygon: tuple[tuple[float, float], ...] = DEFAULT_POLYGON
    count_line_fraction: float = 0.4
    movement_threshold: float = 5.0
    target_fps: float = 8.0
    metrics_interval_s: float = 1.0
    stale_after_s: float = 2.0
    max_track_age_s: float = 5.0
    window_seconds: float = 10.0
    congestion_moderate_threshold: float = 3.0
    congestion_high_threshold: float = 8.0
    congestion_stationary_threshold: float = 0.5
    congestion_moderate_stationary_ratio: float = 0.3


class CameraPipeline:
    """Runs detection → tracking → analytics for one camera on its own thread."""

    def __init__(
        self,
        camera_id: str,
        approach_id: str,
        source: FrameSource,
        detector: Any,
        config: Optional[PipelineConfig] = None,
        *,
        annotate: bool = False,
        draw_stats: bool = True,
    ) -> None:
        self.camera_id = camera_id
        self.approach_id = approach_id
        self.source = source
        self.detector = detector
        self.config = config or PipelineConfig()
        self.annotate = annotate
        # The GUI paints its own corner telemetry; --display keeps these stats.
        self.draw_stats = draw_stats

        self.region = RoadRegion(list(self.config.polygon))
        self.counter = VehicleCounter(
            line_fraction=self.config.count_line_fraction,
            window_seconds=self.config.window_seconds,
        )
        self.congestion = CongestionAnalyzer(
            window_seconds=self.config.window_seconds,
            moderate_threshold=self.config.congestion_moderate_threshold,
            high_threshold=self.config.congestion_high_threshold,
            stationary_threshold=self.config.congestion_stationary_threshold,
            moderate_stationary_ratio=self.config.congestion_moderate_stationary_ratio,
        )
        self.tracker = VehicleTracker(
            movement_threshold=self.config.movement_threshold,
            max_track_age_s=self.config.max_track_age_s,
        )

        self._lock = threading.Lock()
        self._metrics: Optional[ApproachMetrics] = None
        self._annotated: Optional[np.ndarray] = None
        self._last_frame_index = -1
        self._last_process_at: Optional[float] = None
        self._last_publish_at = 0.0
        self._process_times: list[float] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._period = 1.0 / max(0.1, self.config.target_fps)
        self._show_polygon = True
        self._show_count_line = True
        self._reset_requested = threading.Event()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"pipeline-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        _log.info("pipeline %s (approach %s) started", self.camera_id, self.approach_id)

    def request_stop(self) -> None:
        """Ask the worker to exit without waiting for it.

        Lets the orchestrator signal every pipeline before joining any of
        them, so shutdown time is bounded once instead of once per camera.
        """
        self._stop.set()

    def stop(self) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    # -- processing -------------------------------------------------------

    def process_frame(self, frame: Frame) -> ApproachMetrics:
        """Process one explicit frame and store the resulting metrics."""
        started = time.monotonic()
        results = self.detector.detect(frame.image)
        objects = self.tracker.update(results, self.detector.names, now=started)
        expired = self.tracker.prune(started)
        if expired:
            self.counter.prune(expired)

        road_objects = [
            obj
            for obj in objects
            if self.region.contains(obj["center"], frame.image.shape)
        ]

        self.counter.update(road_objects, frame.image.shape, now=started)
        congestion_data = self.congestion.update(road_objects)

        vehicles = [
            obj for obj in road_objects if obj["category"] == "vehicle"
        ]
        stationary_count = sum(
            1 for obj in vehicles if obj["movement"] == "Stationary"
        )
        waits = [obj["stationary_s"] for obj in vehicles]
        mean_wait = round(sum(waits) / len(waits), 2) if waits else 0.0
        max_wait = round(max(waits), 2) if waits else 0.0

        finished = time.monotonic()
        self._process_times.append(finished)
        cutoff = finished - 5.0
        while self._process_times and self._process_times[0] < cutoff:
            self._process_times.pop(0)

        metrics = ApproachMetrics(
            approach_id=self.approach_id,
            camera_id=self.camera_id,
            timestamp=started,
            healthy=True,
            vehicle_count_in_region=len(vehicles),
            stationary_count=stationary_count,
            crossings_last_window=self.counter.crossings_since(
                started - self.config.window_seconds, "vehicle"
            ),
            congestion_level=congestion_data["congestion_level"],
            mean_wait_s=mean_wait,
            max_wait_s=max_wait,
            frame_age_s=round(max(0.0, finished - frame.captured_at), 3),
            frame_index=frame.index,
            processing_fps=self._fps(),
            latency_s=round(finished - started, 3),
        )

        annotated = None
        if self.annotate:
            annotated = self._draw(frame.image, objects, congestion_data["congestion_level"])

        with self._lock:
            self._metrics = metrics
            self._annotated = annotated
            self._last_process_at = finished
            self._last_publish_at = finished

        return metrics

    def latest_metrics(self) -> ApproachMetrics:
        """Latest snapshot with freshness/health recomputed right now."""
        with self._lock:
            base = self._metrics
            last_process_at = self._last_process_at

        health = self.source.health()
        age = health.last_frame_age_s
        now = time.monotonic()

        processing_fresh = (
            last_process_at is not None
            and now - last_process_at <= max(2.0, self._period * 3)
        )
        healthy = (
            health.state is SourceState.LIVE
            and age is not None
            and age <= self.config.stale_after_s
            and processing_fresh
        )

        if base is None:
            return ApproachMetrics(
                approach_id=self.approach_id,
                camera_id=self.camera_id,
                timestamp=now,
                healthy=False,
                frame_age_s=round(age, 3) if age is not None else 0.0,
            )

        return base.with_health(healthy, age if age is not None else 0.0)

    def latest_annotated(self, max_size: Optional[int] = None) -> Optional[np.ndarray]:
        """Copy the annotated frame, optionally downscaled for a GUI tile."""
        with self._lock:
            frame = None if self._annotated is None else self._annotated.copy()
        if frame is None or max_size is None:
            return frame
        height, width = frame.shape[:2]
        longest = max(height, width)
        if longest <= max_size or longest == 0:
            return frame
        scale = max_size / longest
        target = (max(1, int(width * scale)), max(1, int(height * scale)))
        return cv2.resize(frame, target, interpolation=cv2.INTER_AREA)

    def set_overlays(self, *, polygon: bool = True, count_line: bool = True) -> None:
        """Toggle the vector overlays drawn into the annotated copy."""
        self._show_polygon = bool(polygon)
        self._show_count_line = bool(count_line)

    def request_state_reset(self) -> None:
        """Ask the worker to clear counters/tracks; safe from another thread."""
        self._reset_requested.set()

    def reset_state(self) -> None:
        """Clear analysis state now. Call from the worker or a stopped pipeline."""
        self._reset_state_now()

    def _reset_state_now(self) -> None:
        self.counter = VehicleCounter(
            line_fraction=self.config.count_line_fraction,
            window_seconds=self.config.window_seconds,
        )
        self.congestion = CongestionAnalyzer(
            window_seconds=self.config.window_seconds,
            moderate_threshold=self.config.congestion_moderate_threshold,
            high_threshold=self.config.congestion_high_threshold,
            stationary_threshold=self.config.congestion_stationary_threshold,
            moderate_stationary_ratio=self.config.congestion_moderate_stationary_ratio,
        )
        self.tracker = VehicleTracker(
            movement_threshold=self.config.movement_threshold,
            max_track_age_s=self.config.max_track_age_s,
        )
        with self._lock:
            self._metrics = None
            self._annotated = None
            self._last_frame_index = -1
            self._last_process_at = None
            self._process_times.clear()
        _log.info("pipeline %s analysis state reset", self.camera_id)

    # -- worker -----------------------------------------------------------

    def _loop(self) -> None:
        next_due = time.monotonic()
        while not self._stop.is_set():
            if self._reset_requested.is_set():
                self._reset_requested.clear()
                self._reset_state_now()
            frame = self.source.get_latest()
            now = time.monotonic()

            if frame is None:
                self._stop.wait(0.01)
                continue
            if frame.index == self._last_frame_index:
                self._stop.wait(0.005)
                continue
            if now < next_due:
                self._stop.wait(min(next_due - now, 0.02))
                continue

            self._last_frame_index = frame.index
            try:
                self.process_frame(frame)
            except Exception:  # noqa: BLE001 - one bad frame must not kill the loop
                _log.exception("processing failed for camera %s", self.camera_id)

            next_due = time.monotonic() + self._period

    # -- helpers ----------------------------------------------------------

    def _fps(self) -> float:
        if len(self._process_times) < 2:
            return 0.0
        span = self._process_times[-1] - self._process_times[0]
        if span <= 0:
            return 0.0
        return round((len(self._process_times) - 1) / span, 2)

    def _draw(
        self,
        image: np.ndarray,
        objects: Sequence[dict],
        congestion_level: str,
    ) -> np.ndarray:
        canvas = image.copy()
        if self._show_polygon:
            self.region.draw(canvas)

        if self._show_count_line:
            line_y = self.counter.line_y(canvas.shape)
            cv2.line(canvas, (0, line_y), (canvas.shape[1], line_y), (187, 63, 19), 2)

        for obj in objects:
            x1, y1, x2, y2 = (int(value) for value in obj["coordinates"])
            color = (0, 200, 0) if obj["category"] == "vehicle" else (200, 160, 0)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1)
            cx, cy = obj["center"]
            cv2.circle(canvas, (cx, cy), 3, (56, 201, 38), -1)
            cv2.putText(
                canvas,
                f"{obj['track_id']}:{obj['movement'][:4]}",
                (cx, max(12, cy - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (222, 201, 38),
                1,
            )

        if self.draw_stats:
            stats = self.counter.get_statistics()
            lines = [
                f"{self.approach_id} ({self.camera_id})",
                f"Veh in region: {len([o for o in objects if o['category'] == 'vehicle'])}",
                f"Counted: {stats['vehicles']}",
                f"Congestion: {congestion_level}",
            ]
            for index, text in enumerate(lines):
                cv2.putText(
                    canvas,
                    text,
                    (8, 18 + index * 18),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (255, 255, 0) if index else (255, 255, 255),
                    1,
                )
        return canvas
