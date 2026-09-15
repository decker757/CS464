"""One client's socket, and the queue in front of it.

Driven in the test's own event loop with a stand-in for the WebSocket, which is
what makes the overflow deterministic. It cannot be provoked through
`TestClient`: that harness buffers everything the server sends into an unbounded
stream, so `send_json` never blocks, the pump always keeps up and the queue
never fills. Real network backpressure has no equivalent there.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from controller.connection import Connection
from core.errors import SessionExpired, SlowConsumer


class FakeSocket:
    """A WebSocket that records, and can be made to stall on demand."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.unblocked = asyncio.Event()
        self.unblocked.set()

    async def send_json(self, frame: dict[str, Any]) -> None:
        await self.unblocked.wait()
        self.sent.append(frame)


async def test_the_pump_delivers_in_order() -> None:
    """Ordering per socket is the whole reason there is a pump rather than a
    task per send. Two prices for one market arriving out of order on one
    connection is the bug `service/ordering.py` exists to prevent,
    reintroduced one layer lower."""
    socket = FakeSocket()
    connection = Connection(socket, queue_size=8)
    pump = asyncio.create_task(connection.pump())

    for version in range(5):
        connection.enqueue({"state_version": version})

    async with asyncio.timeout(2):
        while len(socket.sent) < 5:
            await asyncio.sleep(0.01)

    pump.cancel()

    assert [frame["state_version"] for frame in socket.sent] == [0, 1, 2, 3, 4]


async def test_enqueue_never_blocks_when_the_socket_is_stalled() -> None:
    """The property `service.subscriptions.Subscriber` requires. It is called
    from inside the fan-out loop, and a call that could block would let one bad
    connection stall delivery to every other subscriber of that market."""
    socket = FakeSocket()
    socket.unblocked.clear()
    connection = Connection(socket, queue_size=4)

    connection.enqueue({"type": "price"})  # must return immediately

    assert connection.pending == 1


async def test_a_full_queue_fails_the_connection() -> None:
    """Dropped rather than buffered. Everything behind a stalled client is a
    price that is already stale, so delivering it late is worse than not
    delivering it: the client reconnects, snapshots, and resumes from the
    truth."""
    connection = Connection(FakeSocket(), queue_size=3)

    for _ in range(4):
        connection.enqueue({"type": "price"})

    async with asyncio.timeout(2):
        failure = await connection.wait_failed()

    assert isinstance(failure, SlowConsumer)


async def test_enqueue_after_a_failure_is_a_no_op() -> None:
    """The fan-out does not check first, and must not have to. A connection
    already marked for closing is still in the hub until its handler unwinds,
    so it will be enqueued to at least once more."""
    connection = Connection(FakeSocket(), queue_size=1)
    connection.fail(SlowConsumer())

    connection.enqueue({"type": "price"})  # must not raise

    assert connection.pending == 0


async def test_the_first_failure_wins() -> None:
    """A socket dropped for an expired token also stops draining its queue, so
    it will overflow moments later. Reporting `slow_consumer` to a client whose
    real problem is an expired session would send it to debug its network
    instead of refreshing its token."""
    connection = Connection(FakeSocket(), queue_size=1)

    connection.fail(SessionExpired())
    connection.fail(SlowConsumer())

    assert isinstance(await connection.wait_failed(), SessionExpired)


async def test_wait_failed_blocks_while_the_connection_is_healthy() -> None:
    """It is one of the four tasks supervising a connection, and a version that
    returned early would close every socket the moment it opened."""
    connection = Connection(FakeSocket(), queue_size=4)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await connection.wait_failed()
