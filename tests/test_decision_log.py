"""JSONL decision log: one JSON object per line, size-rotated."""

from __future__ import annotations

import json

from app.decision_log import DecisionLog


def test_entries_are_one_json_object_per_line(tmp_path):
    path = tmp_path / "decisions.jsonl"
    with DecisionLog(path) as log:
        log.write({"tick": 1, "decision": {"emitted": False}})
        log.write({"tick": 2, "decision": {"emitted": True}})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["tick"] for line in lines] == [1, 2]


def test_log_rotates_by_size(tmp_path):
    path = tmp_path / "decisions.jsonl"
    with DecisionLog(path, max_bytes=300, backup_count=2) as log:
        for tick in range(20):
            log.write({"tick": tick, "payload": "x" * 60})

    assert path.exists()
    assert (tmp_path / "decisions.jsonl.1").exists()
    # Every retained line is still valid JSON.
    for candidate in tmp_path.glob("decisions.jsonl*"):
        for line in candidate.read_text(encoding="utf-8").splitlines():
            json.loads(line)
