"""Unit -> wire adapter: modes, mapping and exact protocol output."""

from __future__ import annotations

import json

import pytest

from decision.models import Recommendation
from gateway.adapter import (
    COMPAT_MODE,
    THREE_PHASE_MODE,
    ProtocolAdapter,
)
from gateway.protocol import MAX_LINE_BYTES, ProtocolError


def recommendation(unit_id: str = "MAIN", green: int = 12) -> Recommendation:
    return Recommendation(
        unit_id=unit_id,
        green_time=green,
        reasons=("test",),
        scores={unit_id: 4.0},
    )


def compat_adapter() -> ProtocolAdapter:
    return ProtocolAdapter(
        COMPAT_MODE,
        {"MAIN": "MAIN", "SECONDARY": "SECONDARY"},
        ["MAIN", "SECONDARY"],
    )


def three_phase_adapter() -> ProtocolAdapter:
    return ProtocolAdapter(
        THREE_PHASE_MODE,
        {"MAIN": "MAIN", "SEC_A": "SECONDARY_A", "SEC_B": "SECONDARY_B"},
        ["MAIN", "SEC_A", "SEC_B"],
        three_phase_enabled=True,
    )


def test_compat_adapter_maps_units_to_the_current_movements():
    adapter = compat_adapter()
    wire = adapter.to_wire(recommendation("SECONDARY", 15))

    assert (wire.unit_id, wire.movement, wire.green_time) == (
        "SECONDARY",
        "SECONDARY",
        15,
    )
    assert wire.priority == "NORMAL"
    assert adapter.wire_movements == ("MAIN", "SECONDARY")
    assert adapter.startup_warnings() == ()


def test_three_phase_adapter_uses_the_proposed_names():
    adapter = three_phase_adapter()

    assert adapter.to_wire(recommendation("SEC_A", 20)).movement == "SECONDARY_A"
    assert adapter.wire_movements == ("MAIN", "SECONDARY_A", "SECONDARY_B")
    assert any("NOT implemented" in warning for warning in adapter.startup_warnings())


def test_three_phase_requires_the_explicit_opt_in():
    with pytest.raises(ValueError, match="disabled"):
        ProtocolAdapter(
            THREE_PHASE_MODE,
            {"MAIN": "MAIN", "SEC_A": "SECONDARY_A", "SEC_B": "SECONDARY_B"},
            ["MAIN", "SEC_A", "SEC_B"],
            three_phase_enabled=False,
        )


def test_compat_adapter_refuses_non_firmware_movements():
    with pytest.raises(ValueError, match="cannot send"):
        ProtocolAdapter(
            COMPAT_MODE,
            {"MAIN": "MAIN", "SEC_A": "SECONDARY_A"},
            ["MAIN", "SEC_A"],
        )


def test_adapter_requires_a_movement_for_every_unit():
    with pytest.raises(ValueError, match="no wire movement"):
        ProtocolAdapter(COMPAT_MODE, {"MAIN": "MAIN"}, ["MAIN", "SECONDARY"])


def test_adapter_rejects_duplicate_wire_movements():
    with pytest.raises(ValueError, match="unique"):
        ProtocolAdapter(
            THREE_PHASE_MODE,
            {"MAIN": "MAIN", "SEC_A": "SIDE", "SEC_B": "SIDE"},
            ["MAIN", "SEC_A", "SEC_B"],
            three_phase_enabled=True,
        )


def test_unknown_unit_is_a_protocol_error():
    with pytest.raises(ProtocolError):
        compat_adapter().to_wire(recommendation("SEC_A"))


def test_build_message_is_compact_newline_terminated_and_within_the_limit():
    adapter = three_phase_adapter()
    message = adapter.build_message(recommendation("SEC_B", 15), sequence=104)
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"

    assert message == {
        "type": "TRAFFIC_REQUEST",
        "movement": "SECONDARY_B",
        "green_time": 15,
        "priority": "NORMAL",
        "sequence": 104,
    }
    assert b" " not in payload
    assert payload.endswith(b"\n")
    assert len(payload) <= MAX_LINE_BYTES


def test_adapter_refuses_green_time_outside_the_protocol():
    with pytest.raises(ProtocolError):
        compat_adapter().to_wire(recommendation("MAIN", 45))
