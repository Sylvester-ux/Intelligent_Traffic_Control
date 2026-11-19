"""Camera layer tests: three simultaneous sources, staleness and reconnect."""

from __future__ import annotations

import time

import numpy as np
import pytest

from camera.base import (
    SourceOpenError,
    SourceState,
    ThreadedFrameSource,
)
from camera.file_source import FileSource
from camera.manager import CameraManager
from camera.rtsp import redact_url
from tests.conftest import wait_until


def test_three_file_sources_run_simultaneously(make_video):
    paths = [make_video(f"cam{i}.avi", start_x=5 * i + 5) for i in range(3)]
    sources = {
        f"cam{i}": FileSource(f"cam{i}", paths[i], loop=True, realtime=False)
        for i in range(3)
    }
    manager = CameraManager(sources)
    try:
        manager.start_all()
        assert wait_until(
            lambda: all(manager.get_latest(c) is not None for c in manager.camera_ids),
            timeout_s=5.0,
        ), "sources never produced a frame"

        for camera_id in manager.camera_ids:
            frame = manager.get_latest(camera_id)
            assert frame is not None
            assert frame.camera_id == camera_id
            assert frame.image.shape == (240, 320, 3)

        # Each source keeps advancing independently.
        before = {c: manager.get_latest(c).index for c in manager.camera_ids}
        time.sleep(0.2)
        after = {c: manager.get_latest(c).index for c in manager.camera_ids}
        for camera_id in manager.camera_ids:
            assert after[camera_id] > before[camera_id]

        assert manager.live_camera_ids(max_age_s=2.0) == set(manager.camera_ids)
        health = manager.health_all()
        assert all(h.state is SourceState.LIVE for h in health.values())
        assert all(h.fps > 0 for h in health.values())
    finally:
        manager.stop_all()

    assert all(
        manager.health(c).state is SourceState.DISCONNECTED
        for c in manager.camera_ids
    )


class _OnceThenSlowSource(ThreadedFrameSource):
    """Publishes one frame, then blocks long enough to look stale."""

    def _open(self) -> None:
        pass

    def _read(self):
        time.sleep(1.0)
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def _close(self) -> None:
        pass


def test_source_goes_stale_without_frames():
    source = _OnceThenSlowSource("slow", stale_after_s=0.15)
    try:
        source.start()
        assert wait_until(lambda: source.get_latest() is not None, timeout_s=3.0)
        assert source.health().state is SourceState.LIVE
        assert wait_until(
            lambda: source.health().state is SourceState.STALE, timeout_s=3.0
        ), f"expected STALE, got {source.health().state}"
    finally:
        source.stop()


class _FlakySource(ThreadedFrameSource):
    """Fails to open twice, then succeeds, to exercise backoff."""

    def __init__(self, camera_id: str) -> None:
        super().__init__(camera_id, reconnect_min_s=0.01, reconnect_max_s=0.02)
        self.attempts = 0

    def _open(self) -> None:
        self.attempts += 1
        if self.attempts <= 2:
            raise SourceOpenError("not ready")

    def _read(self):
        return True, np.zeros((4, 4, 3), dtype=np.uint8)

    def _close(self) -> None:
        pass


def test_source_reconnects_after_open_failures():
    source = _FlakySource("flaky")
    try:
        source.start()
        assert wait_until(
            lambda: source.health().state is SourceState.LIVE, timeout_s=3.0
        )
        assert source.attempts >= 3
        assert source.health().reconnects >= 1
    finally:
        source.stop()


def test_rtsp_url_password_is_redacted():
    url = "rtsp://admin:supersecret@192.168.50.11:554/stream2"
    redacted = redact_url(url)
    assert "supersecret" not in redacted
    assert "192.168.50.11" in redacted
    assert redact_url("rtsp://192.168.50.11:554/stream2").endswith("/stream2")


def test_rtsp_source_rejects_non_rtsp_url():
    from camera.rtsp import RtspSource

    with pytest.raises(ValueError):
        RtspSource("cam", "http://192.168.50.11/stream")
