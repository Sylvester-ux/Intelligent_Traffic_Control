"""Wire protocol: newline-delimited compact JSON, 512 bytes per line.

Pure encoding/decoding with no sockets, so it is cheap to test. The framing
handles the two TCP realities that break naive readers: a message split across
``recv`` calls, and several messages arriving in one ``recv``. Oversized lines
are discarded to the next newline instead of desynchronising the stream.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

MAX_LINE_BYTES = 512

MOVEMENTS = ("MAIN", "SECONDARY")
PRIORITY = "NORMAL"
GREEN_MIN = 5
GREEN_MAX = 30

#: Error reasons the gateway may return for a traffic request.
ERROR_REASONS = (
    "AI_LINK_OFFLINE",
    "INVALID_FIELDS",
    "OUT_OF_POLICY",
    "STALE_SEQUENCE",
    "UNO_ACK_TIMEOUT",
    "UNO_NACK",
)


class ProtocolError(ValueError):
    """Raised when a message cannot be encoded or is structurally invalid."""


def encode_message(message: dict) -> bytes:
    """Encode one compact JSON message, newline-terminated."""
    try:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not JSON serialisable: {exc}") from exc
    line = payload.encode("utf-8")
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError(
            f"encoded message is {len(line)} bytes, limit is {MAX_LINE_BYTES}"
        )
    return line + b"\n"


def decode_message(line: str | bytes) -> dict:
    """Decode one line into a message dict."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"line is not UTF-8: {exc}") from exc
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    if not isinstance(message.get("type"), str):
        raise ProtocolError("message is missing a string 'type'")
    return message


class LineBuffer:
    """Reassembles newline-terminated messages from arbitrary chunks."""

    def __init__(self, max_bytes: int = MAX_LINE_BYTES) -> None:
        self.max_bytes = max_bytes
        self._buffer = bytearray()
        self._discarding = False
        self.dropped = 0

    def feed(self, data: bytes) -> list[str]:
        """Add a chunk and return every complete message line it completed."""
        if not data:
            return []
        self._buffer.extend(data)
        lines: list[str] = []

        while True:
            newline = self._buffer.find(b"\n")
            if newline == -1:
                if len(self._buffer) > self.max_bytes:
                    # A single line is over budget: drop it and everything up to
                    # the next newline, rather than growing the buffer.
                    self._buffer.clear()
                    self._discarding = True
                    self.dropped += 1
                break

            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]

            if self._discarding:
                self._discarding = False
                continue
            if len(raw) > self.max_bytes:
                self.dropped += 1
                continue

            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)

        return lines


def heartbeat_message() -> dict:
    return {"type": "HEARTBEAT"}


def status_request_message() -> dict:
    return {"type": "STATUS_REQUEST"}


def traffic_request_message(
    movement: str,
    green_time: int,
    sequence: int,
    movements: Iterable[str] = MOVEMENTS,
) -> dict:
    """Build and validate a TRAFFIC_REQUEST (``priority`` is always NORMAL).

    ``movements`` defaults to what the current firmware accepts. The protocol
    adapter may pass a hypothetical set when explicitly configured; see
    ``gateway/adapter.py``.
    """
    allowed = tuple(movements)
    if movement not in allowed:
        raise ProtocolError(f"movement must be one of {allowed}")
    if not isinstance(green_time, int) or isinstance(green_time, bool):
        raise ProtocolError("green_time must be an integer")
    if not GREEN_MIN <= green_time <= GREEN_MAX:
        raise ProtocolError(
            f"green_time must be within {GREEN_MIN}-{GREEN_MAX} seconds"
        )
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise ProtocolError("sequence must be a positive integer")
    return {
        "type": "TRAFFIC_REQUEST",
        "movement": movement,
        "green_time": green_time,
        "priority": PRIORITY,
        "sequence": sequence,
    }


def traffic_request_error(
    message: dict, movements: Iterable[str] = MOVEMENTS
) -> Optional[str]:
    """Validate a received TRAFFIC_REQUEST; return an error reason or ``None``."""
    if message.get("movement") not in tuple(movements):
        return "INVALID_FIELDS"
    green_time = message.get("green_time")
    if not isinstance(green_time, int) or isinstance(green_time, bool):
        return "INVALID_FIELDS"
    if not GREEN_MIN <= green_time <= GREEN_MAX:
        return "OUT_OF_POLICY"
    if message.get("priority") != PRIORITY:
        return "INVALID_FIELDS"
    sequence = message.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        return "INVALID_FIELDS"
    return None


def ack_heartbeat() -> dict:
    return {"type": "GATEWAY_ACK", "status": "HEARTBEAT_OK"}


def ack_status(detail: str) -> dict:
    return {"type": "UNO_STATUS", "detail": detail}


def ack_accepted(sequence: int, uno_ack: str) -> dict:
    return {
        "type": "GATEWAY_ACK",
        "status": "CONTROLLER_ACCEPTED",
        "sequence": sequence,
        "uno_ack": uno_ack,
    }


def error_message(reason: str) -> dict:
    return {"type": "ERROR", "reason": reason}


def is_heartbeat_ack(message: dict) -> bool:
    return (
        message.get("type") == "GATEWAY_ACK"
        and message.get("status") == "HEARTBEAT_OK"
    )


def is_accepted_ack(message: dict) -> bool:
    return (
        message.get("type") == "GATEWAY_ACK"
        and message.get("status") == "CONTROLLER_ACCEPTED"
    )


def uno_ack_name(movement: str) -> str:
    """The Uno echoes ``ACK:REQUEST_MAIN`` / ``ACK:REQUEST_SIDE``."""
    return "SIDE" if movement == "SECONDARY" else "MAIN"


def format_lines(messages: Iterable[dict]) -> bytes:
    """Convenience for tests and the mock: encode several messages at once."""
    return b"".join(encode_message(message) for message in messages)
