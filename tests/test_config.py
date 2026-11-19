"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import (
    CameraConfig,
    ConfigError,
    build_config,
    build_source,
    load_config,
)
from camera.file_source import FileSource
from camera.rtsp import RtspSource
from camera.webcam import WebcamSource
from decision.models import GREEN_TIME_MAX, GREEN_TIME_MIN


ENV = {"CAM_USER": "camera", "CAM_PASS": "secret"}


def test_example_config_loads_with_env_credentials():
    config = load_config("config.example.yaml", env=ENV)

    assert config.camera_ids == ("cam_main", "cam_sec_a", "cam_sec_b")
    assert config.approaches == {
        "MAIN": "MAIN",
        "SEC_A": "SECONDARY",
        "SEC_B": "SECONDARY",
    }
    assert config.required_camera_count == 3
    assert config.gateway.host == "192.168.4.1"
    assert config.gateway.port == 3333
    assert config.decision.aggregation == "sum"
    assert GREEN_TIME_MIN <= config.decision.min_green_s
    assert config.decision.max_green_s <= GREEN_TIME_MAX
    assert config.cameras[0].url.startswith("rtsp://camera:secret@192.168.50.11")
    assert config.phase_mode == "two_phase_compat"


def test_missing_environment_variable_is_reported():
    with pytest.raises(ConfigError) as excinfo:
        load_config("config.example.yaml", env={})
    message = str(excinfo.value)
    assert "CAM_USER" in message and "CAM_PASS" in message


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _camera(**overrides) -> dict:
    camera = {
        "id": "cam_main",
        "approach": "MAIN",
        "source": "file",
        "path": "clip.avi",
    }
    camera.update(overrides)
    return camera


def test_relative_file_paths_resolve_against_the_config_file(tmp_path):
    path = _write(
        tmp_path,
        {"cameras": [_camera()], "approaches": {"MAIN": "MAIN"}},
    )
    config = load_config(path, env={})
    assert Path(config.cameras[0].path) == tmp_path / "clip.avi"


def test_two_cameras_cannot_share_an_approach():
    raw = {
        "cameras": [_camera(), _camera(id="cam_other")],
        "approaches": {"MAIN": "MAIN"},
    }
    with pytest.raises(ConfigError, match="one camera per approach"):
        build_config(raw)


def test_unknown_movement_is_rejected():
    raw = {
        "cameras": [_camera()],
        "approaches": {"MAIN": "NORTH"},
    }
    with pytest.raises(ConfigError, match="unknown movement"):
        build_config(raw)


def test_camera_approach_must_be_mapped():
    raw = {"cameras": [_camera()], "approaches": {"SEC_A": "SECONDARY"}}
    with pytest.raises(ConfigError, match="missing from 'approaches'"):
        build_config(raw)


def test_duplicate_camera_ids_are_rejected():
    raw = {
        "cameras": [_camera(), _camera(approach="SEC_A")],
        "approaches": {"MAIN": "MAIN", "SEC_A": "SECONDARY"},
    }
    with pytest.raises(ConfigError, match="unique"):
        build_config(raw)


def test_unknown_keys_are_rejected():
    raw = {
        "cameras": [_camera()],
        "approaches": {"MAIN": "MAIN"},
        "gateway": {"host": "127.0.0.1", "portt": 3333},
    }
    with pytest.raises(ConfigError, match="unknown keys"):
        build_config(raw)


def test_three_phase_mode_is_not_supported():
    raw = {
        "cameras": [_camera()],
        "approaches": {"MAIN": "MAIN"},
        "phase_mode": "three_phase",
    }
    with pytest.raises(ConfigError, match="three-phase"):
        build_config(raw)


def test_green_range_must_stay_inside_the_protocol():
    raw = {
        "cameras": [_camera()],
        "approaches": {"MAIN": "MAIN"},
        "decision": {"max_green_s": 40},
    }
    with pytest.raises(ConfigError, match="green time"):
        build_config(raw)

    narrowed = build_config(
        {
            "cameras": [_camera()],
            "approaches": {"MAIN": "MAIN"},
            "decision": {"min_green_s": 8, "max_green_s": 25},
        }
    )
    assert (narrowed.decision.min_green_s, narrowed.decision.max_green_s) == (8, 25)


def test_polygon_must_be_normalized():
    raw = {
        "cameras": [
            _camera(polygon=[[0.1, 0.3], [0.9, 0.3], [1.5, 0.95]])
        ],
        "approaches": {"MAIN": "MAIN"},
    }
    with pytest.raises(ConfigError, match="normalized"):
        build_config(raw)


def test_file_camera_needs_a_path():
    camera = _camera()
    camera.pop("path")
    with pytest.raises(ConfigError, match="without 'path'"):
        build_config({"cameras": [camera], "approaches": {"MAIN": "MAIN"}})


def test_source_factory_builds_each_type():
    common = {"id": "cam", "approach": "MAIN"}
    webcam = build_source(CameraConfig(source="webcam", index=1, **common))
    rtsp = build_source(
        CameraConfig(source="rtsp", url="rtsp://user:pass@10.0.0.2/stream", **common)
    )
    file = build_source(CameraConfig(source="file", path="clip.avi", **common))

    assert isinstance(webcam, WebcamSource)
    assert webcam.index == 1
    assert isinstance(rtsp, RtspSource)
    # The password must not leak through repr/log helpers.
    assert rtsp.health_message() == "rtsp://user:***@10.0.0.2/stream"
    assert isinstance(file, FileSource)
    assert file.path.name == "clip.avi"
