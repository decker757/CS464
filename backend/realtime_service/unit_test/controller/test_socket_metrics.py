"""The open-websocket gauge: up on an authenticated accept, down on every ending.
#599

The registry is process-wide, so each test compares against its own starting
reading. The app runs in another thread, so the decrement after a client
leaves is polled for rather than read at once.
"""

from __future__ import annotations

import time

import pytest
from prometheus_client import REGISTRY
from starlette.websockets import WebSocketDisconnect

from core.errors import NotAuthenticated
from unit_test.conftest import bearer

OPEN_SOCKETS = "realtime_open_websockets"
_ALLOWED_ORIGIN = "http://localhost:5173"


def _open_sockets() -> float:
    value = REGISTRY.get_sample_value(OPEN_SOCKETS)
    assert value is not None, f"{OPEN_SOCKETS} is not registered"
    return value


def _wait_for(expected: float) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and _open_sockets() != expected:
        time.sleep(0.01)


def test_an_open_socket_is_counted_and_a_closed_one_is_not(client) -> None:
    start = _open_sockets()
    headers = {**bearer(), "origin": _ALLOWED_ORIGIN}

    with client.websocket_connect("/ws/prices", headers=headers):
        _wait_for(start + 1)
        assert _open_sockets() == start + 1

    _wait_for(start)
    assert _open_sockets() == start


def test_a_refused_socket_is_never_counted(client) -> None:
    start = _open_sockets()

    with client.websocket_connect("/ws/prices") as websocket:
        with pytest.raises(WebSocketDisconnect) as refused:
            websocket.receive_json()
        assert _open_sockets() == start

    assert refused.value.code == NotAuthenticated.close_code
    assert _open_sockets() == start


def test_a_socket_from_an_unlisted_origin_is_never_counted(client) -> None:
    start = _open_sockets()
    headers = {**bearer(), "origin": "http://evil.test"}

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/prices", headers=headers) as websocket:
            websocket.receive_json()

    assert _open_sockets() == start
