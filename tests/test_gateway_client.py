"""Gateway client against the mock: handshake, sequences, errors, framing."""

from __future__ import annotations

import socket
import time

import pytest

from gateway.client import GatewayClient, GatewayConfig
from gateway.mock_gateway import MockGateway
from tests.conftest import wait_until


def config_for(mock: MockGateway, **overrides) -> GatewayConfig:
    defaults = dict(
        host="127.0.0.1",
        port=mock.port,
        connect_timeout_s=0.5,
        handshake_timeout_s=1.0,
        request_timeout_s=1.0,
        heartbeat_interval_s=0.1,
        reconnect_min_s=0.05,
        reconnect_max_s=0.2,
        request_backoff_s=0.05,
    )
    defaults.update(overrides)
    return GatewayConfig(**defaults)


@pytest.fixture
def mock():
    gateway = MockGateway(port=0)
    gateway.start()
    try:
        yield gateway
    finally:
        gateway.stop()


def start_client(mock, health=lambda: True, **overrides):
    client = GatewayClient(config_for(mock, **overrides), health)
    client.start()
    assert wait_until(lambda: client.connected, timeout_s=3.0), "no session"
    return client


def test_handshake_then_accepted_request(mock):
    client = start_client(mock)
    try:
        result = client.request_green("MAIN", 15)
        assert result.ok, result
        assert result.status == "CONTROLLER_ACCEPTED"
        assert result.uno_ack == "ACK:REQUEST_MAIN,15"
        assert result.sequence == 1

        requests = mock.traffic_requests
        assert len(requests) == 1
        assert requests[0]["sequence"] == 1
        assert requests[0]["priority"] == "NORMAL"
        assert requests[0]["movement"] == "MAIN"
    finally:
        client.stop()


def test_sequence_increases_per_request(mock):
    client = start_client(mock)
    try:
        first = client.request_green("MAIN", 10)
        second = client.request_green("SECONDARY", 12)
        assert first.sequence == 1
        assert second.sequence == 2
        assert [item["sequence"] for item in mock.traffic_requests] == [1, 2]
    finally:
        client.stop()


def test_reconnect_resets_sequence(mock):
    client = start_client(mock)
    try:
        assert client.request_green("MAIN", 10).ok
        port = mock.port
        mock.stop()
        assert wait_until(lambda: not client.connected, timeout_s=3.0)

        replacement = MockGateway(port=port)
        replacement.start()
        try:
            assert wait_until(lambda: client.connected, timeout_s=5.0)
            result = client.request_green("MAIN", 10)
            assert result.ok, result
            assert result.sequence == 1, "sequence must restart at 1 per session"
            assert replacement.traffic_requests[0]["sequence"] == 1
        finally:
            replacement.stop()
    finally:
        client.stop()


def test_heartbeat_gate_stops_heartbeats_when_unhealthy():
    gateway = MockGateway(port=0, heartbeat_freshness_s=0.2)
    gateway.start()
    healthy = {"value": True}
    client = GatewayClient(
        config_for(gateway, heartbeat_interval_s=0.05),
        lambda: healthy["value"],
    )
    try:
        client.start()
        assert wait_until(lambda: client.connected, timeout_s=3.0)
        assert wait_until(lambda: gateway.heartbeats >= 3, timeout_s=3.0)

        healthy["value"] = False
        # Let the freshness window lapse, then requests must be refused.
        time.sleep(0.5)
        before = gateway.heartbeats
        time.sleep(0.3)
        assert gateway.heartbeats == before, "heartbeat kept running while unhealthy"
        result = client.request_green("MAIN", 10)
        assert not result.ok
        assert result.status == "AI_LINK_OFFLINE"
    finally:
        client.stop()
        gateway.stop()


def test_unhealthy_at_start_never_opens_the_gate():
    gateway = MockGateway(port=0, heartbeat_freshness_s=0.2)
    gateway.start()
    client = GatewayClient(
        config_for(gateway, heartbeat_interval_s=0.05), lambda: False
    )
    try:
        client.start()
        assert wait_until(lambda: client.connected, timeout_s=3.0)
        time.sleep(0.4)
        # Only the handshake heartbeat was sent.
        assert gateway.heartbeats == 1
        result = client.request_green("MAIN", 10)
        assert not result.ok
        assert result.status == "AI_LINK_OFFLINE"
    finally:
        client.stop()
        gateway.stop()


def test_uno_nack_is_reported_and_backed_off():
    gateway = MockGateway(port=0, nack_rate=1.0, seed=1)
    gateway.start()
    client = start_client(gateway)
    try:
        result = client.request_green("MAIN", 10)
        assert not result.ok
        assert result.status == "UNO_NACK"

        immediate = client.request_green("MAIN", 10)
        assert immediate.status == "BACKOFF"
    finally:
        client.stop()
        gateway.stop()


def test_uno_ack_timeout_is_reported():
    gateway = MockGateway(port=0, timeout_rate=1.0, seed=1)
    gateway.start()
    client = start_client(gateway)
    try:
        result = client.request_green("SECONDARY", 20)
        assert not result.ok
        assert result.status == "UNO_ACK_TIMEOUT"
    finally:
        client.stop()
        gateway.stop()


def test_client_rejects_out_of_range_green_without_sending(mock):
    client = start_client(mock)
    try:
        result = client.request_green("MAIN", 45)
        assert not result.ok
        assert result.status == "INVALID_FIELDS"
        assert mock.traffic_requests == []
    finally:
        client.stop()


def test_out_of_policy_reply_from_gateway():
    # Bypass the client's own validation by driving the mock directly.
    gateway = MockGateway(port=0)
    gateway.start()
    sock = socket.create_connection(("127.0.0.1", gateway.port), timeout=2.0)
    sock.settimeout(2.0)
    try:
        sock.sendall(b'{"type":"HEARTBEAT"}\n')
        assert _read_message(sock)["status"] == "HEARTBEAT_OK"
        sock.sendall(
            b'{"type":"TRAFFIC_REQUEST","movement":"MAIN","green_time":99,'
            b'"priority":"NORMAL","sequence":1}\n'
        )
        reply = _read_message(sock)
        assert reply == {"type": "ERROR", "reason": "OUT_OF_POLICY"}
    finally:
        sock.close()
        gateway.stop()


def test_split_and_merged_chunks_on_the_wire():
    gateway = MockGateway(port=0)
    gateway.start()
    sock = socket.create_connection(("127.0.0.1", gateway.port), timeout=2.0)
    sock.settimeout(2.0)
    try:
        # One heartbeat split across two sends.
        sock.sendall(b'{"type":"HEA')
        sock.sendall(b'RTBEAT"}\n')
        assert _read_message(sock)["status"] == "HEARTBEAT_OK"

        # Two heartbeats in a single send.
        sock.sendall(b'{"type":"HEARTBEAT"}\n{"type":"HEARTBEAT"}\n')
        assert _read_message(sock)["status"] == "HEARTBEAT_OK"
        assert _read_message(sock)["status"] == "HEARTBEAT_OK"
    finally:
        sock.close()
        gateway.stop()


def test_oversized_line_is_discarded_then_stream_recovers():
    gateway = MockGateway(port=0)
    gateway.start()
    sock = socket.create_connection(("127.0.0.1", gateway.port), timeout=2.0)
    sock.settimeout(3.0)
    try:
        sock.sendall(b"z" * 700 + b"\n")
        sock.sendall(b'{"type":"HEARTBEAT"}\n')
        reply = _read_message(sock)
        assert reply["status"] == "HEARTBEAT_OK"
    finally:
        sock.close()
        gateway.stop()


def test_second_client_is_refused():
    gateway = MockGateway(port=0)
    gateway.start()
    first = socket.create_connection(("127.0.0.1", gateway.port), timeout=2.0)
    try:
        first.sendall(b'{"type":"HEARTBEAT"}\n')
        assert _read_message(first)["status"] == "HEARTBEAT_OK"

        second = socket.create_connection(("127.0.0.1", gateway.port), timeout=2.0)
        second.settimeout(1.0)
        try:
            assert second.recv(1) == b""
        except socket.timeout:
            pass
        second.close()
    finally:
        first.close()
        gateway.stop()


def _read_message(sock: socket.socket) -> dict:
    import json

    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("connection closed before a full line arrived")
        buffer += chunk
    line, _, _rest = buffer.partition(b"\n")
    return json.loads(line)
