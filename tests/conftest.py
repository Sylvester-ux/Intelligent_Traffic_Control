"""Shared test helpers: synthetic videos and polling utilities."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pytest


def write_test_video(
    path: Path,
    *,
    frames: int = 60,
    width: int = 320,
    height: int = 240,
    fps: float = 15.0,
    start_x: int = 10,
    step: int = 6,
    start_y: int = 100,
    step_y: int = 0,
    rect_width: int = 30,
    rect_height: int = 50,
    moving: bool = True,
) -> Path:
    """Render a tiny MJPG clip with one bright rectangle on a dark background."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height)
    )
    if not writer.isOpened():  # pragma: no cover - codec availability
        pytest.skip("OpenCV MJPG writer unavailable in this build")
    for i in range(frames):
        image = np.zeros((height, width, 3), dtype=np.uint8)
        x = (start_x + i * step) % max(1, width - rect_width - 10) if moving else start_x
        y = min(max(0, start_y + i * step_y), height - rect_height - 1)
        cv2.rectangle(
            image,
            (x, y),
            (x + rect_width, y + rect_height),
            (0, 255, 0),
            -1,
        )
        writer.write(image)
    writer.release()
    return path


def wait_until(
    predicate: Callable[[], bool], timeout_s: float = 5.0, interval_s: float = 0.01
) -> bool:
    """Poll ``predicate`` until true or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


@pytest.fixture
def make_video(tmp_path):
    """Return a factory that writes synthetic clips into ``tmp_path``."""

    def _make(name: str = "clip.avi", **kwargs) -> Path:
        return write_test_video(tmp_path / name, **kwargs)

    return _make
