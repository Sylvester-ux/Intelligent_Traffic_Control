"""Per-track movement analysis with bounded, per-camera state.

One :class:`VehicleTracker` instance belongs to exactly one camera pipeline.
It owns no global state, and expired tracks are pruned so a long run does not
grow without bound (the previous version leaked every track id forever).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from detection.object_classifier import classify_object


@dataclass
class _TrackState:
    first_seen: float
    last_seen: float
    stationary_since: float
    last_center: tuple[int, int]
    movement: str


class VehicleTracker:
    """Turns Ultralytics tracking results into per-object movement records."""

    def __init__(
        self,
        movement_threshold: float = 5.0,
        max_track_age_s: float = 5.0,
    ) -> None:
        self.movement_threshold = movement_threshold
        self.max_track_age_s = max_track_age_s
        self.previous_centers: dict[int, tuple[int, int]] = {}
        self._tracks: dict[int, _TrackState] = {}

    def update(
        self,
        results: Any,
        class_names: dict[int, str] | Iterable[str],
        now: Optional[float] = None,
    ) -> list[dict]:
        """Return one record per tracked object in the current frame."""
        if now is None:
            now = time.monotonic()

        boxes = results[0].boxes
        frame_objects: list[dict] = []

        if boxes is None or boxes.id is None:
            return frame_objects

        track_ids = boxes.id.int().cpu().tolist()
        class_ids = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        coordinates = boxes.xyxy.cpu().tolist()

        for track_id, class_id, confidence, coordinate in zip(
            track_ids, class_ids, confidences, coordinates
        ):
            object_name = class_names[class_id]
            category = classify_object(class_id)

            center_x = int((coordinate[0] + coordinate[2]) / 2)
            center_y = int((coordinate[1] + coordinate[3]) / 2)
            current_center = (center_x, center_y)

            state = self._tracks.get(track_id)
            old_center: Optional[tuple[int, int]] = None

            if state is None:
                displacement = 0.0
                movement_status = "Initializing"
                state = _TrackState(
                    first_seen=now,
                    last_seen=now,
                    stationary_since=now,
                    last_center=current_center,
                    movement=movement_status,
                )
                self._tracks[track_id] = state
            else:
                old_center = state.last_center
                displacement = (
                    (center_x - old_center[0]) ** 2
                    + (center_y - old_center[1]) ** 2
                ) ** 0.5
                if displacement > self.movement_threshold:
                    movement_status = "Moving"
                    state.stationary_since = now
                else:
                    movement_status = "Stationary"
                state.last_seen = now
                state.last_center = current_center
                state.movement = movement_status

            self.previous_centers[track_id] = current_center

            stationary_s = (
                max(0.0, now - state.stationary_since)
                if movement_status == "Stationary"
                else 0.0
            )

            frame_objects.append(
                {
                    "track_id": track_id,
                    "class_id": class_id,
                    "object_name": object_name,
                    "category": category,
                    "confidence": round(confidence, 3),
                    "coordinates": coordinate,
                    "previous_center": old_center,
                    "center": current_center,
                    "displacement": round(displacement, 2),
                    "movement": movement_status,
                    "stationary_s": round(stationary_s, 2),
                    "first_seen": state.first_seen,
                }
            )

        return frame_objects

    def prune(self, now: Optional[float] = None) -> list[int]:
        """Drop tracks unseen for too long; return the expired track ids."""
        if now is None:
            now = time.monotonic()
        expired = [
            track_id
            for track_id, state in self._tracks.items()
            if now - state.last_seen > self.max_track_age_s
        ]
        for track_id in expired:
            self._tracks.pop(track_id, None)
            self.previous_centers.pop(track_id, None)
        return expired

    def reset(self) -> None:
        self.previous_centers.clear()
        self._tracks.clear()

    @property
    def active_track_ids(self) -> set[int]:
        return set(self._tracks)
