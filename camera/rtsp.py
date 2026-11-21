"""RTSP camera source for the wired Ethernet cameras.

OpenCV's FFmpeg backend is configured explicitly:

* ``rtsp_transport;tcp`` avoids UDP packet loss and NAT issues on a switch.
* A small buffer plus the latest-frame policy keeps latency low.
* Open/read timeouts stop a dead camera from wedging the capture thread.
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from camera.base import SourceOpenError, ThreadedFrameSource

_log = logging.getLogger(__name__)

_ENV_KEY = "OPENCV_FFMPEG_CAPTURE_OPTIONS"


def ensure_tcp_transport() -> None:
    """Force FFmpeg's RTSP demuxer onto TCP via the OpenCV env option."""
    current = os.environ.get(_ENV_KEY, "")
    if "rtsp_transport" in current:
        return
    os.environ[_ENV_KEY] = (
        f"{current}|rtsp_transport;tcp" if current else "rtsp_transport;tcp"
    )


def redact_url(url: str) -> str:
    """Hide the password in a URL before it reaches a log."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}", ":***", 1)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class RtspSource(ThreadedFrameSource):
    """One capture thread per RTSP URL, with reconnect and TCP transport."""

    def __init__(
        self,
        camera_id: str,
        url: str,
        *,
        flip: bool = False,
        width: Optional[int] = None,
        height: Optional[int] = None,
        open_timeout_s: float = 5.0,
        read_timeout_s: float = 5.0,
        stale_after_s: float = 2.0,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 10.0,
        max_width: Optional[int] = None,
    ) -> None:
        if not url.startswith(("rtsp://", "rtsps://")):
            raise ValueError(f"RtspSource needs an rtsp(s) URL, got {redact_url(url)!r}")
        super().__init__(
            camera_id,
            stale_after_s=stale_after_s,
            reconnect_min_s=reconnect_min_s,
            reconnect_max_s=reconnect_max_s,
            flip=flip,
            max_width=max_width,
        )
        self.url = url
        self.width = width
        self.height = height
        self.open_timeout_s = open_timeout_s
        self.read_timeout_s = read_timeout_s
        self._capture: Optional[cv2.VideoCapture] = None

    def _open(self) -> None:
        ensure_tcp_transport()
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            int(self.open_timeout_s * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            int(self.read_timeout_s * 1000),
        ]
        capture: Optional[cv2.VideoCapture] = None
        try:
            capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        except (TypeError, cv2.error) as exc:  # pragma: no cover - backend specific
            _log.debug("ffmpeg params unsupported (%s); using defaults", exc)

        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            capture = cv2.VideoCapture(self.url)

        if not capture.isOpened():
            capture.release()
            raise SourceOpenError(f"could not open {redact_url(self.url)}")

        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:  # pragma: no cover - not every backend supports it
            pass
        if self.width and self.height:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        self._capture = capture
        _log.info("opened %s", redact_url(self.url))

    def _read(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._capture is None:
            return False, None
        try:
            return self._capture.read()
        except cv2.error:
            return False, None

    def _close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def health_message(self) -> str:
        return redact_url(self.url)
