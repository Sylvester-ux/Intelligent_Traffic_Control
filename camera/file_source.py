"""Video-file source used for development and for hardware-free tests."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from camera.base import SourceOpenError, ThreadedFrameSource

_log = logging.getLogger(__name__)


class FileSource(ThreadedFrameSource):
    """Replay a video file, optionally looping and paced to real frame rate.

    ``realtime=False`` reads as fast as the disk allows, which keeps tests fast.
    """

    def __init__(
        self,
        camera_id: str,
        path: str | Path,
        *,
        loop: bool = True,
        realtime: bool = True,
        flip: bool = False,
        stale_after_s: float = 2.0,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 10.0,
    ) -> None:
        super().__init__(
            camera_id,
            stale_after_s=stale_after_s,
            reconnect_min_s=reconnect_min_s,
            reconnect_max_s=reconnect_max_s,
            flip=flip,
        )
        self.path = Path(path)
        self.loop = loop
        self.realtime = realtime
        self._capture: Optional[cv2.VideoCapture] = None
        self._file_fps = 25.0
        self._next_due = 0.0

    def _open(self) -> None:
        if not self.path.is_file():
            raise SourceOpenError(f"video file not found: {self.path}")
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            raise SourceOpenError(f"could not open video file: {self.path}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        self._file_fps = fps if fps and fps > 0 else 25.0
        self._next_due = time.monotonic()
        self._capture = capture
        _log.info("opened %s (%.1f fps)", self.path, self._file_fps)

    def _read(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._capture is None:
            return False, None

        ok, frame = self._capture.read()
        if not ok:
            if not self.loop:
                return False, None
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._next_due = time.monotonic()
            ok, frame = self._capture.read()
            if not ok:
                return False, None

        self._pace()
        return True, frame

    def _pace(self) -> None:
        """Sleep until the next frame is due so playback matches the file rate."""
        if not self.realtime:
            return
        now = time.monotonic()
        if self._next_due < now:
            self._next_due = now
        delay = self._next_due - now
        if delay > 0:
            self._stop.wait(delay)
        self._next_due += 1.0 / self._file_fps

    def _close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
