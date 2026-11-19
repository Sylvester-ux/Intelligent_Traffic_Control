"""YOLO detection + tracking wrapper.

**One instance per camera.** ``model.track(..., persist=True)`` stores ByteTrack
state on the model object, so sharing a ``VehicleDetector`` between cameras
interleaves their track ids. Each ``CameraPipeline`` therefore owns its own
detector (and its own model weights in memory). See
``tests/test_pipeline_isolation.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ultralytics import YOLO

_log = logging.getLogger(__name__)


def auto_device() -> str:
    """Return ``cuda`` when a GPU is usable, otherwise ``cpu``."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 - a broken torch install must not be fatal
        _log.warning("torch CUDA probe failed; using CPU", exc_info=True)
    return "cpu"


class VehicleDetector:
    """Wraps a YOLO model with tracking enabled."""

    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        *,
        device: Optional[str] = None,
        confidence: float = 0.3,
        imgsz: Optional[int] = None,
        tracker: str = "bytetrack.yaml",
    ) -> None:
        self.model_path = model_path
        self.device = device or auto_device()
        self.confidence = confidence
        self.imgsz = imgsz
        self.tracker = tracker
        self.model = YOLO(model_path)
        _log.info("loaded %s on %s", model_path, self.device)

    @property
    def names(self) -> dict[int, str]:
        return self.model.names

    def detect(self, frame: Any) -> Any:
        """Run detection + tracking on one BGR frame."""
        kwargs: dict[str, Any] = {
            "conf": self.confidence,
            "verbose": False,
            "persist": True,
            "tracker": self.tracker,
            "device": self.device,
        }
        if self.imgsz:
            kwargs["imgsz"] = self.imgsz
        return self.model.track(frame, **kwargs)
