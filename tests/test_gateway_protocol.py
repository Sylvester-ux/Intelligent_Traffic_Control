"""Framing and field validation for the gateway wire protocol."""

from __future__ import annotations

import pytest

from gateway.protocol import (
    MAX_LINE_BYTES,
    LineBuffer,
    ProtocolError,
    decode_message,
    encode_message,
    error_message,
    traffic_request_error,
    traffic_request_message,
)


def test_encode_decode_roundtrip_is_compact_and_newline_terminated():
    payload = encode_message({"type": "HEARTBEAT"})
    assert payload == b'{"type":"HEARTBEAT"}\n'
    assert decode_message(payload.strip()) == {"type": "HEARTBEAT"}


def test_encode_rejects_lines_over_the_limit():
    oversized = {"type": "ERROR", "reason": "x" * MAX_LINE_BYTES}
    with pytest.raises(ProtocolError):
        encode_message(oversized)


def test_decode_rejects_non_objects_and_bad_json():
    with pytest.raises(ProtocolError):
        decode_message("[1,2,3]")
    with pytest.raises(ProtocolError):
        decode_message("{not json")
    with pytest.raises(ProtocolError):
        decode_message('{"no_type":1}')


def test_line_buffer_reassembles_split_chunks():
    buffer = LineBuffer()
    assert buffer.feed(b'{"type":"HEA') == []
    assert buffer.feed(b'RTBEAT"}\n') == ['{"type":"HEARTBEAT"}']


def test_line_buffer_handles_several_messages_in_one_chunk():
    buffer = LineBuffer()
    pages = buffer.feed(b'{"type":"HEARTBEAT"}\n{"type":"STATUS_REQUEST"}\n')
    assert pages == ['{"type":"HEARTBEAT"}', '{"type":"STATUS_REQUEST"}']


def test_line_buffer_discards_oversized_line_and_recovers():
    buffer = LineBuffer()
    chunk = b"x" * (MAX_LINE_BYTES + 10) + b"\n" + b'{"type":"HEARTBEAT"}\n'
    assert buffer.feed(chunk) == ['{"type":"HEARTBEAT"}']
    assert buffer.dropped == 1


def test_line_buffer_discards_oversized_line_without_newline_then_recovers():
    buffer = LineBuffer()
    assert buffer.feed(b"y" * (MAX_LINE_BYTES + 50)) == []
    assert buffer.feed(b"still-the-same-line\n") == []
    assert buffer.feed(b'{"type":"HEARTBEAT"}\n') == ['{"type":"HEARTBEAT"}']


def test_blank_lines_are_ignored():
    buffer = LineBuffer()
    assert buffer.feed(b"\n\n") == []


def test_traffic_request_is_always_normal_priority():
    message = traffic_request_message("SECONDARY", 15, 104)
    assert message == {
        "type": "TRAFFIC_REQUEST",
        "movement": "SECONDARY",
        "green_time": 15,
        "priority": "NORMAL",
        "sequence": 104,
    }
    assert traffic_request_error(message) is None


@pytest.mark.parametrize(
    "message,reason",
    [
        ({"movement": "NORTH", "green_time": 10, "priority": "NORMAL", "sequence": 1}, "INVALID_FIELDS"),
        ({"movement": "MAIN", "green_time": 4, "priority": "NORMAL", "sequence": 1}, "OUT_OF_POLICY"),
        ({"movement": "MAIN", "green_time": 31, "priority": "NORMAL", "sequence": 1}, "OUT_OF_POLICY"),
        ({"movement": "MAIN", "green_time": 10, "priority": "HIGH", "sequence": 1}, "INVALID_FIELDS"),
        ({"movement": "MAIN", "green_time": 10, "priority": "NORMAL", "sequence": 0}, "INVALID_FIELDS"),
        ({"movement": "MAIN", "green_time": "10", "priority": "NORMAL", "sequence": 1}, "INVALID_FIELDS"),
    ],
)
def test_traffic_request_validation(message, reason):
    assert traffic_request_error(message) == reason


def test_traffic_request_builder_rejects_bad_fields():
    with pytest.raises(ProtocolError):
        traffic_request_message("NORTH", 10, 1)
    with pytest.raises(ProtocolError):
        traffic_request_message("MAIN", 40, 1)
    with pytest.raises(ProtocolError):
        traffic_request_message("MAIN", 10, 0)


def test_error_message_shape():
    assert error_message("UNO_NACK") == {"type": "ERROR", "reason": "UNO_NACK"}
