"""Camera inputs: webcam, RTSP and video files behind one interface."""

from camera.base import (
    Frame,
    FrameSource,
    SourceHealth,
    SourceOpenError,
    SourceState,
    ThreadedFrameSource,
)
from camera.file_source import FileSource
from camera.manager import CameraManager
from camera.rtsp import RtspSource, redact_url
from camera.webcam import Webcam, WebcamSource

__all__ = [
    "CameraManager",
    "FileSource",
    "Frame",
    "FrameSource",
    "RtspSource",
    "SourceHealth",
    "SourceOpenError",
    "SourceState",
    "ThreadedFrameSource",
    "Webcam",
    "WebcamSource",
    "redact_url",
]
