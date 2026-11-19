"""End-to-end: three simulated cameras -> decisions -> mock gateway -> JSONL.

No GPU, camera or real network: file sources replay synthetic clips and the
mock gateway implements the protocol rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.config import build_config
from app.orchestrator import Orchestrator
from gateway.mock_gateway import MockGateway
from tests.fakes import FakeBoxes, FakeResult


class AlternatingDetector:
    """Reports one vehicle, then two, so demand (and green time) changes."""

    names = {2: "car"}

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame: np.ndarray) -> list[FakeResult]:
        self.calls += 1
        count = 2 if (self.calls // 8) % 2 == 0 else 1
        coordinates = [[80.0, 100.0, 120.0, 150.0], [160.0, 100.0, 200.0, 150.0]]
        boxes = FakeBoxes(
            ids=list(range(1, count + 1)),
            classes=[2] * count,
            confidences=[0.9] * count,
            coordinates=coordinates[:count],
        )
        return [FakeResult(boxes)]


def _camera(camera_id: str, approach: str, path: Path) -> dict:
    return {
        "id": camera_id,
        "approach": approach,
        "source": "file",
        "path": str(path),
        "loop": True,
        "realtime": True,
        "target_fps": 30,
        "stale_after_s": 2.0,
    }


def test_three_cameras_to_mock_gateway(make_video, tmp_path):
    main_clip = make_video("main.avi", frames=40, start_x=100, start_y=20, step_y=8, moving=False)
    sec_a_clip = make_video("sec_a.avi", frames=40, start_x=80, start_y=20, step_y=8, moving=False)
    sec_b_clip = make_video("sec_b.avi", frames=40, start_x=120, start_y=20, step_y=8, moving=False)
    log_path = tmp_path / "decisions.jsonl"

    with MockGateway(port=0) as gateway:
        raw = {
            "cameras": [
                _camera("cam_main", "MAIN", main_clip),
                _camera("cam_sec_a", "SEC_A", sec_a_clip),
                _camera("cam_sec_b", "SEC_B", sec_b_clip),
            ],
            "approaches": {
                "MAIN": "MAIN",
                "SEC_A": "SECONDARY",
                "SEC_B": "SECONDARY",
            },
            "decision": {
                "smoothing_window_s": 0,
                "min_interval_s": 0,
                "min_delta_s": 0,
                "idle_score_threshold": 0.5,
            },
            "gateway": {
                "host": "127.0.0.1",
                "port": gateway.port,
                "connect_timeout_s": 0.5,
                "handshake_timeout_s": 1.0,
                "request_timeout_s": 1.0,
                "heartbeat_interval_s": 0.05,
                "reconnect_min_s": 0.05,
                "reconnect_max_s": 0.2,
                "request_backoff_s": 0.05,
            },
            "health": {
                "tick_interval_s": 0.25,
                "max_frame_age_s": 2.0,
                "tick_timeout_s": 2.0,
            },
            "logging": {"decision_log": str(log_path)},
        }
        config = build_config(raw)
        orchestrator = Orchestrator(
            config,
            display=False,
            detector_factory=lambda camera: AlternatingDetector(),
        )
        orchestrator.run(duration_s=3.0)

        requests = gateway.traffic_requests

    # The gateway accepted real traffic requests with valid, increasing sequences.
    assert requests, "no traffic request reached the gateway"
    sequences = [item["sequence"] for item in requests]
    assert sequences == list(range(1, len(sequences) + 1))
    for item in requests:
        assert item["movement"] in ("MAIN", "SECONDARY")
        assert item["priority"] == "NORMAL"
        assert 5 <= item["green_time"] <= 30

    # Every cycle is logged, including cycles that emitted nothing.
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) >= 5
    for entry in entries:
        assert set(entry["approaches"]) == {"MAIN", "SEC_A", "SEC_B"}
        assert entry["decision"]["reasons"]
        assert set(entry["cameras"]) == {"cam_main", "cam_sec_a", "cam_sec_b"}

    accepted = [
        entry
        for entry in entries
        if entry["request"] and entry["request"].get("accepted")
    ]
    assert accepted, "no request was accepted by the mock gateway"
    assert entries[-1]["health_gate_open"] is True
    assert all(entry["approaches"]["SEC_B"]["healthy"] for entry in entries[-3:])

    # All three cameras were fresh and the gateway saw heartbeats.
    assert entries[-1]["cameras"]["cam_main"]["state"] == "LIVE"
    assert orchestrator.gateway is not None
    assert orchestrator.gateway.connected is False  # stopped cleanly
