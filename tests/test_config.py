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
from decision.models import GREEN_TIME_MAX, GREEN_TIME_MIN, ServingUnit


ENV = {"CAM_USER": "camera", "CAM_PASS": "secret"}


def test_example_config_loads_with_env_credentials():
    config = load_config("config.example.yaml", env=ENV)

    assert config.camera_ids == ("cam_main", "cam_sec_a", "cam_sec_b")
    assert config.units == (
        ServingUnit("MAIN", ("MAIN",)),
        ServingUnit("SECONDARY", ("SEC_A", "SEC_B")),
    )
    assert config.required_camera_count == 3
    assert config.gateway.host == "192.168.4.1"
    assert config.gateway.port == 3333
    assert config.decision.aggregation == "sum"
    assert GREEN_TIME_MIN <= config.decision.min_green_s
    assert config.decision.max_green_s <= GREEN_TIME_MAX
    assert config.cameras[0].url.startswith("rtsp://camera:secret@192.168.50.11")
    # The old ambiguous mode switch is gone; the layout lives in units.
    assert not hasattr(config, "phase_mode")


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


def _units(*entries: tuple[str, tuple[str, ...]]) -> list[dict]:
    return [
        {"id": unit_id, "approaches": list(approaches)}
        for unit_id, approaches in entries
    ]


def _raw(**overrides) -> dict:
    raw = {
        "cameras": [_camera()],
        "decision": {"units": _units(("MAIN", ("MAIN",)))},
    }
    raw.update(overrides)
    return raw


def test_relative_file_paths_resolve_against_the_config_file(tmp_path):
    path = _write(tmp_path, _raw())
    config = load_config(path, env={})
    assert Path(config.cameras[0].path) == tmp_path / "clip.avi"


def test_two_cameras_cannot_share_an_approach():
    raw = {
        "cameras": [_camera(), _camera(id="cam_other")],
        "decision": {"units": _units(("MAIN", ("MAIN",)))},
    }
    with pytest.raises(ConfigError, match="one camera per approach"):
        build_config(raw)


def test_compat_mode_requires_the_firmware_unit_names():
    raw = _raw(decision={"units": _units(("NORTH", ("MAIN",)))})
    with pytest.raises(ConfigError, match="compat_two_phase units"):
        build_config(raw)


def test_camera_approach_must_be_assigned_to_a_unit():
    raw = _raw(
        cameras=[
            _camera(),
            _camera(id="cam_sec", approach="SEC_A"),
        ],
        decision={"units": _units(("MAIN", ("MAIN",)))},
    )
    with pytest.raises(ConfigError, match="missing from decision.units"):
        build_config(raw)


def test_unit_may_not_reference_an_approach_without_a_camera():
    raw = _raw(
        decision={"units": _units(("MAIN", ("MAIN", "SEC_A")))},
    )
    with pytest.raises(ConfigError, match="no camera"):
        build_config(raw)


def test_an_approach_may_not_be_in_two_units():
    raw = _raw(
        decision={
            "units": _units(
                ("MAIN", ("MAIN",)),
                ("SECONDARY", ("MAIN",)),
            )
        }
    )
    with pytest.raises(ConfigError, match="assigned to both"):
        build_config(raw)


def test_duplicate_camera_ids_are_rejected():
    raw = _raw(
        cameras=[_camera(), _camera(approach="SEC_A")],
        decision={
            "units": _units(
                ("MAIN", ("MAIN",)),
                ("SECONDARY", ("SEC_A",)),
            )
        },
    )
    with pytest.raises(ConfigError, match="unique"):
        build_config(raw)


def test_unknown_keys_are_rejected():
    raw = _raw(gateway={"host": "127.0.0.1", "portt": 3333})
    with pytest.raises(ConfigError, match="unknown keys"):
        build_config(raw)


def test_legacy_layout_keys_are_rejected_with_a_migration_hint():
    with pytest.raises(ConfigError, match="decision.units"):
        build_config(
            {
                "cameras": [_camera()],
                "approaches": {"MAIN": "MAIN"},
                "decision": {"units": _units(("MAIN", ("MAIN",)))},
            }
        )
    with pytest.raises(ConfigError, match="gateway.mode"):
        build_config(
            {
                "cameras": [_camera()],
                "phase_mode": "two_phase_compat",
                "decision": {"units": _units(("MAIN", ("MAIN",)))},
            }
        )


def test_green_range_must_stay_inside_the_protocol():
    raw = _raw(decision={"units": _units(("MAIN", ("MAIN",))), "max_green_s": 40})
    with pytest.raises(ConfigError, match="green time"):
        build_config(raw)

    narrowed = build_config(
        _raw(decision={"units": _units(("MAIN", ("MAIN",))), "min_green_s": 8, "max_green_s": 25})
    )
    assert (narrowed.decision.min_green_s, narrowed.decision.max_green_s) == (8, 25)


def test_polygon_must_be_normalized():
    raw = _raw(
        cameras=[_camera(polygon=[[0.1, 0.3], [0.9, 0.3], [1.5, 0.95]])]
    )
    with pytest.raises(ConfigError, match="normalized"):
        build_config(raw)


def test_file_camera_needs_a_path():
    camera = _camera()
    camera.pop("path")
    with pytest.raises(ConfigError, match="without 'path'"):
        build_config(_raw(cameras=[camera]))


def test_decision_units_must_be_a_non_empty_list():
    with pytest.raises(ConfigError, match="non-empty list"):
        build_config(_raw(decision={"units": []}))
    with pytest.raises(ConfigError, match="non-empty list"):
        build_config({"cameras": [_camera()]})


THREE_PHASE_UNITS = _units(
    ("MAIN", ("MAIN",)),
    ("SEC_A", ("SEC_A",)),
    ("SEC_B", ("SEC_B",)),
)
THREE_PHASE_MOVEMENTS = {
    "MAIN": "MAIN",
    "SEC_A": "SECONDARY_A",
    "SEC_B": "SECONDARY_B",
}


def _three_phase_cameras() -> list[dict]:
    return [
        _camera(),
        _camera(id="cam_sec_a", approach="SEC_A"),
        _camera(id="cam_sec_b", approach="SEC_B"),
    ]


def _three_phase_raw(**gateway) -> dict:
    gateway.setdefault("mode", "three_phase")
    gateway.setdefault(
        "three_phase",
        {"enabled": True, "movements": dict(THREE_PHASE_MOVEMENTS)},
    )
    return {
        "cameras": _three_phase_cameras(),
        "decision": {"units": THREE_PHASE_UNITS},
        "gateway": gateway,
    }


def test_three_phase_layout_loads_when_explicitly_enabled():
    config = build_config(_three_phase_raw())

    assert config.gateway_mode == "three_phase"
    assert config.three_phase_enabled is True
    assert config.gateway_movements == THREE_PHASE_MOVEMENTS
    assert [unit.id for unit in config.units] == ["MAIN", "SEC_A", "SEC_B"]


def test_three_phase_is_refused_while_disabled():
    raw = _three_phase_raw()
    raw["gateway"]["three_phase"]["enabled"] = False

    with pytest.raises(ConfigError, match="enabled is false"):
        build_config(raw)


def test_three_phase_needs_explicit_movement_names():
    raw = _three_phase_raw()
    del raw["gateway"]["three_phase"]["movements"]["SEC_B"]

    with pytest.raises(ConfigError, match="explicitly name every unit"):
        build_config(raw)


def test_three_units_are_rejected_in_compat_mode():
    raw = _three_phase_raw(mode="compat_two_phase")
    raw["gateway"].pop("three_phase")

    with pytest.raises(ConfigError, match="compat_two_phase units"):
        build_config(raw)


def test_enabling_three_phase_while_in_compat_mode_is_an_error():
    raw = _raw(
        gateway={
            "mode": "compat_two_phase",
            "three_phase": {"enabled": True},
        }
    )
    with pytest.raises(ConfigError, match="choose gateway.mode"):
        build_config(raw)


def test_unknown_gateway_mode_is_rejected():
    with pytest.raises(ConfigError, match="gateway.mode must be"):
        build_config(_raw(gateway={"mode": "split_phase"}))


def test_three_phase_movements_must_be_unique_and_complete():
    duplicate = _three_phase_raw()
    duplicate["gateway"]["three_phase"]["movements"] = {
        "MAIN": "MAIN",
        "SEC_A": "SIDE",
        "SEC_B": "SIDE",
    }
    with pytest.raises(ConfigError, match="unique"):
        build_config(duplicate)

    extra = _three_phase_raw()
    extra["gateway"]["three_phase"]["movements"]["NORTH"] = "NORTH"
    with pytest.raises(ConfigError, match="unknown units"):
        build_config(extra)


def test_unknown_three_phase_keys_are_rejected():
    raw = _three_phase_raw()
    raw["gateway"]["three_phase"]["magic"] = True
    with pytest.raises(ConfigError, match="unknown keys"):
        build_config(raw)


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
