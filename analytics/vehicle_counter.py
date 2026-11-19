"""Virtual-line object counting for a single camera.

The counting line is a *normalized* y position (0 = top, 1 = bottom) so it is
independent of the camera resolution, and every crossing is timestamped so the
decision engine can ask for "crossings in the last N seconds" instead of only a
cumulative total.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Iterable, Optional


class VehicleCounter:
    """Counts line crossings once per track id, in either direction."""

    def __init__(self, line_fraction: float = 0.4, window_seconds: float = 10.0) -> None:
        if not 0.0 <= line_fraction <= 1.0:
            raise ValueError("line_fraction must be within [0, 1]")
        self.line_fraction = line_fraction
        self.window_seconds = window_seconds
        self.counted_ids: set[int] = set()
        self._counts: dict[str, int] = defaultdict(int)
        self._crossings: list[tuple[float, str]] = []

    def line_y(self, frame_shape) -> int:
        """Pixel y of the counting line for a given frame shape."""
        return int(self.line_fraction * frame_shape[0])

    def update(self, frame_objects: Iterable[dict], frame_shape, now: Optional[float] = None) -> None:
        """Register crossings for this frame's tracked objects."""
        if now is None:
            now = time.monotonic()

        line_y = self.line_y(frame_shape)

        for obj in frame_objects:
            track_id = obj["track_id"]
            previous_center = obj.get("previous_center")
            current_center = obj["center"]

            if previous_center is None or track_id in self.counted_ids:
                continue

            old_y = previous_center[1]
            current_y = current_center[1]

            crossed = (old_y < line_y <= current_y) or (old_y >= line_y >= current_y)
            if not crossed:
                continue

            self.counted_ids.add(track_id)
            category = obj.get("category", "other")
            self._counts[category] += 1
            self._crossings.append((now, category))

        self._trim(now)

    def crossings_since(
        self, since: float, category: Optional[str] = None
    ) -> int:
        """Crossings at or after ``since`` (optionally filtered by category)."""
        return sum(
            1
            for when, name in self._crossings
            if when >= since and (category is None or name == category)
        )

    def prune(self, stale_track_ids: Iterable[int]) -> None:
        """Forget ids of tracks that have left the scene.

        The set would otherwise grow forever, and a recycled ByteTrack id would
        be treated as already counted.
        """
        for track_id in stale_track_ids:
            self.counted_ids.discard(track_id)

    def get_statistics(self) -> dict:
        return {
            "vehicles": self._counts.get("vehicle", 0),
            "pedestrians": self._counts.get("pedestrian", 0),
            "others": sum(
                count
                for category, count in self._counts.items()
                if category not in ("vehicle", "pedestrian")
            ),
            "total": sum(self._counts.values()),
        }

    def _trim(self, now: float) -> None:
        cutoff = now - max(self.window_seconds * 2.0, 30.0)
        if len(self._crossings) > 64:
            self._crossings = [
                (when, category)
                for when, category in self._crossings
                if when >= cutoff
            ]


# Backwards-compatible name used by the original single-camera demo.
Vehicle_counter = VehicleCounter
