"""Minimal stand-ins for Ultralytics results, so tests need no model or GPU."""

from __future__ import annotations

from typing import Any, Iterable, Optional

import numpy as np


class _TensorLike:
    """Mimics the ``.int().cpu().tolist()`` chain used by the tracker."""

    def __init__(self, values: Iterable[Any]) -> None:
        self._values = list(values)

    def int(self) -> "_TensorLike":
        return _TensorLike(int(value) for value in self._values)

    def cpu(self) -> "_TensorLike":
        return self

    def tolist(self) -> list:
        return list(self._values)


class FakeBoxes:
    def __init__(
        self,
        ids: Optional[Iterable[int]],
        classes: Iterable[int],
        confidences: Iterable[float],
        coordinates: Iterable[Iterable[float]],
    ) -> None:
        self.id = _TensorLike(ids) if ids is not None else None
        self.cls = _TensorLike(classes)
        self.conf = _TensorLike(confidences)
        self.xyxy = _TensorLike(coordinates)


class FakeResult:
    def __init__(self, boxes: Optional[FakeBoxes]) -> None:
        self.boxes = boxes


class FakeDetector:
    """Returns a fixed list of ``(track_id, class_id, xyxy)`` boxes per call."""

    names: dict[int, str] = {2: "car", 0: "person", 3: "motorcycle"}

    def __init__(self, detections: Iterable[tuple[int, int, list[float]]] = ()) -> None:
        self.detections = list(detections)
        self.calls = 0

    def set_detections(self, detections: Iterable[tuple[int, int, list[float]]]) -> None:
        self.detections = list(detections)

    def detect(self, frame) -> list[FakeResult]:
        self.calls += 1
        detections = list(self.detections)
        if not detections:
            return [FakeResult(None)]
        boxes = FakeBoxes(
            ids=[det[0] for det in detections],
            classes=[det[1] for det in detections],
            confidences=[0.9 for _ in detections],
            coordinates=[det[2] for det in detections],
        )
        return [FakeResult(boxes)]


class GreenRectDetector:
    """Finds the bright green rectangle rendered by ``write_test_video``.

    Every frame reports the same track id, which is exactly what makes the
    pipeline-isolation test meaningful: if state leaked between pipelines, the
    second camera would inherit the first camera's "already counted" id.
    """

    names: dict[int, str] = {2: "car"}

    def __init__(self, track_id: int = 1) -> None:
        self.track_id = track_id
        self.calls = 0

    def detect(self, frame: np.ndarray) -> list[FakeResult]:
        self.calls += 1
        mask = (frame[:, :, 1] > 150) & (frame[:, :, 0] < 80) & (frame[:, :, 2] < 80)
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return [FakeResult(None)]
        box = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
        boxes = FakeBoxes(
            ids=[self.track_id],
            classes=[2],
            confidences=[0.9],
            coordinates=[box],
        )
        return [FakeResult(boxes)]
