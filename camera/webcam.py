"""Local USB / built-in webcam capture."""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from camera.base import SourceOpenError, SourceState, ThreadedFrameSource

_log = logging.getLogger(__name__)


class Webcam:
    """Minimal single-camera wrapper kept for the legacy demo in ``main.py``."""

    def __init__(self, camera_index: int = 0, flip: bool = True) -> None:
        self.camera_index = camera_index
        self.flip = flip
        self.camera: Optional[cv2.VideoCapture] = None
        self.open()

    def open(self) -> None:
        self.camera = cv2.VideoCapture(self.camera_index)

    def is_opened(self) -> bool:
        return bool(self.camera is not None and self.camera.isOpened())

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        if self.camera is None:
            return False, None
        success, frame = self.camera.read()
        if success and self.flip:
            frame = cv2.flip(frame, 1)
        return success, frame

    def release(self) -> None:
        if self.camera is not None:
            self.camera.release()
            self.camera = None

    def set_resolution(self, width: int, height: int) -> None:
        if self.camera is None:
            return
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)


class WebcamSource(ThreadedFrameSource):
    """Capture thread around :class:`Webcam`, with per-camera flip support."""

    def __init__(
        self,
        camera_id: str = "webcam",
        index: int = 0,
        *,
        flip: bool = True,
        width: Optional[int] = None,
        height: Optional[int] = None,
        stale_after_s: float = 2.0,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 10.0,
        max_width: Optional[int] = None,
    ) -> None:
        super().__init__(
            camera_id,
            stale_after_s=stale_after_s,
            reconnect_min_s=reconnect_min_s,
            reconnect_max_s=reconnect_max_s,
            flip=flip,
            max_width=max_width,
        )
        self.index = index
        self.width = width
        self.height = height
        self._webcam: Optional[Webcam] = None

    def _open(self) -> None:
        # Flipping is applied once by the base class, so do not flip here.
        webcam = Webcam(self.index, flip=False)
        if not webcam.is_opened():
            webcam.release()
            raise SourceOpenError(f"could not open webcam index {self.index}")
        if self.width and self.height:
            webcam.set_resolution(self.width, self.height)
        self._webcam = webcam

    def _read(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._webcam is None:
            return False, None
        return self._webcam.read()

    def _close(self) -> None:
        if self._webcam is not None:
            self._webcam.release()
            self._webcam = None
