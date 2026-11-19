"""ESP32 gateway link: protocol, client and a hardware-free mock gateway."""

from gateway.client import GatewayClient, GatewayConfig, RequestResult
from gateway.protocol import (
    MAX_LINE_BYTES,
    LineBuffer,
    ProtocolError,
    decode_message,
    encode_message,
    traffic_request_message,
)

__all__ = [
    "GatewayClient",
    "GatewayConfig",
    "LineBuffer",
    "MAX_LINE_BYTES",
    "ProtocolError",
    "RequestResult",
    "decode_message",
    "encode_message",
    "traffic_request_message",
]
