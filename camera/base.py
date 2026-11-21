"""Frame source abstraction shared by every camera input.

Every source runs one capture thread that keeps only the *latest* frame: stale
frames are dropped rather than queued, so a slow consumer can never build a
backlog. Each published frame carries the camera id, a monotonic capture
timestamp and a per-source frame counter.

The status state machine is derived from the capture thread's behaviour:

``CONNECTING``  -> no frame yet and the source is trying to open
``LIVE``        -> frames are arriving
``STALE``       -> the last frame is older than ``stale_after_s``
``DISCONNECTED``-> the source is closed and backing off before a retry
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np


class SourceState(str, Enum):
    """Lifecycle state of a single frame source."""

    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class Frame:
    """A captured frame plus the metadata needed to detect staleness."""

    camera_id: str
    image: np.ndarray
    captured_at: float
    index: int


@dataclass(frozen=True)
class SourceHealth:
    """Point-in-time health snapshot for one source."""

    camera_id: str
    state: SourceState
    fps: float
    last_frame_age_s: Optional[float]
    reconnects: int


class SourceOpenError(RuntimeError):
    """Raised internally when a source cannot open its capture device."""


class FrameSource(ABC):
    """Interface implemented by every camera input type."""

    def __init__(self, camera_id: str, stale_after_s: float = 2.0) -> None:
        self.camera_id = camera_id
        self.stale_after_s = stale_after_s
        self._lock = threading.Lock()
        self._frame: Optional[Frame] = None
        self._frame_times: list[float] = []
        self._index = 0
        self._reconnects = 0

    @abstractmethod
    def start(self) -> None:
        """Start capturing in the background. Must be idempotent."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capturing and release resources."""

    @abstractmethod
    def get_latest(self) -> Optional[Frame]:
        """Return the most recent frame, or ``None`` before the first frame."""

    @abstractmethod
    def health(self) -> SourceHealth:
        """Return a health snapshot for this source."""

    # -- helpers shared with subclasses -----------------------------------

    def _publish(self, image: np.ndarray) -> None:
        now = time.monotonic()
        with self._lock:
            self._index += 1
            self._frame = Frame(
                camera_id=self.camera_id,
                image=image,
                captured_at=now,
                index=self._index,
            )
            self._frame_times.append(now)
            # Keep ~2 s of timestamps for an fps estimate.
            cutoff = now - 2.0
            while self._frame_times and self._frame_times[0] < cutoff:
                self._frame_times.pop(0)

    def _last_frame(self) -> Optional[Frame]:
        with self._lock:
            return self._frame

    def _note_reconnect(self) -> None:
        with self._lock:
            self._reconnects += 1

    def _estimate_fps(self) -> float:
        with self._lock:
            times = list(self._frame_times)
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        if span <= 0:
            return 0.0
        return round((len(times) - 1) / span, 2)

    def _age(self) -> Optional[float]:
        frame = self._last_frame()
        if frame is None:
            return None
        return max(0.0, time.monotonic() - frame.captured_at)

    def _state_from_age(self, open_: bool, ever_framed: bool) -> SourceState:
        if not open_:
            return SourceState.DISCONNECTED
        if not ever_framed:
            return SourceState.CONNECTING
        age = self._age()
        if age is not None and age > self.stale_after_s:
            return SourceState.STALE
        return SourceState.LIVE

    def _health(self, state: SourceState) -> SourceHealth:
        return SourceHealth(
            camera_id=self.camera_id,
            state=state,
            fps=self._estimate_fps(),
            last_frame_age_s=self._age(),
            reconnects=self._reconnect_count(),
        )

    def _reconnect_count(self) -> int:
        with self._lock:
            return self._reconnects


class ThreadedFrameSource(FrameSource):
    """Common capture-thread implementation with reconnect and backoff.

    Subclasses implement :meth:`_open`, :meth:`_read` and :meth:`_close`.
    """

    def __init__(
        self,
        camera_id: str,
        *,
        stale_after_s: float = 2.0,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 10.0,
        flip: bool = False,
        max_width: Optional[int] = None,
    ) -> None:
        super().__init__(camera_id=camera_id, stale_after_s=stale_after_s)
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.flip = flip
        # Downscale once here, before a full-resolution frame is retained.
        self.max_width = None if max_width is None else max(160, int(max_width))
        self._log = logging.getLogger(f"camera.{camera_id}")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_open = False
        self._ever_framed = False
        self._opened_once = False
        self._backoff_s = reconnect_min_s
        self._lock_open = threading.Lock()

    # -- subclass hooks ---------------------------------------------------

    @abstractmethod
    def _open(self) -> None:
        """Open the capture device. Raise :class:`SourceOpenError` on failure."""

    @abstractmethod
    def _read(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read one frame. Return ``(ok, frame)``."""

    @abstractmethod
    def _close(self) -> None:
        """Release the capture device. Must be safe to call twice."""

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        with self._lock_open:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._is_open = False
            self._ever_framed = False
            self._open_attempts = 0
            self._backoff_s = self.reconnect_min_s
            self._thread = threading.Thread(
                target=self._run,
                name=f"camera-{self.camera_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._close()
        with self._lock_open:
            self._is_open = False

    def get_latest(self) -> Optional[Frame]:
        return self._last_frame()

    def health(self) -> SourceHealth:
        with self._lock_open:
            open_ = self._is_open
        return self._health(self._state_from_age(open_, self._ever_framed))

    # -- capture loop -----------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._is_open:
                if not self._try_open():
                    continue
            try:
                ok, image = self._read()
            except Exception as exc:  # noqa: BLE001 - backend errors are opaque
                self._log.warning("read failed: %s", exc, exc_info=True)
                ok, image = False, None
            if not ok or image is None:
                self._log.warning("stream ended; reconnecting")
                self._teardown()
                self._wait_backoff()
                continue
            if self.flip:
                image = cv2.flip(image, 1)
            if self.max_width is not None and image.shape[1] > self.max_width:
                scale = self.max_width / image.shape[1]
                image = cv2.resize(
                    image,
                    (
                        self.max_width,
                        max(1, int(round(image.shape[0] * scale))),
                    ),
                    interpolation=cv2.INTER_AREA,
                )
            self._publish(image)
            self._ever_framed = True
            self._backoff_s = self.reconnect_min_s

    def _try_open(self) -> bool:
        self._open_attempts += 1
        try:
            self._open()
        except SourceOpenError as exc:
            self._log.warning("open failed: %s", exc)
            self._is_open = False
            self._wait_backoff()
            return False
        except Exception as exc:  # noqa: BLE001
            self._log.warning("unexpected open error: %s", exc, exc_info=True)
            self._is_open = False
            self._wait_backoff()
            return False
        with self._lock_open:
            self._is_open = True
        if self._open_attempts > 1:
            self._note_reconnect()
            self._log.info("connected after retries")
        self._opened_once = True
        return True

    def _teardown(self) -> None:
        try:
            self._close()
        finally:
            with self._lock_open:
                self._is_open = False

    def _wait_backoff(self) -> None:
        delay = self._backoff_s
        self._backoff_s = min(self._backoff_s * 2, self.reconnect_max_s)
        if self._stop.wait(delay):
            return
