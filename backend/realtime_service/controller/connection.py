"""One client's socket, and the queue in front of it. [F-2] #42

This is the concrete `service.subscriptions.Subscriber`. It lives in the
controller because everything in it is transport: a WebSocket, a send buffer
and the frames on the wire. The hub that decides *which* connections get an
event knows none of this.

**Why there is a queue at all.** The obvious implementation broadcasts by
awaiting `send_json` on each subscriber in turn. One client on a bad connection
then stalls the loop, and every other subscriber to that market waits behind it
— on a feed whose entire value is being faster than a page refresh. The
alternative of firing a task per send does not block, but it gives up ordering:
two prices for one market can land out of order on one socket, which is exactly
the bug `service/ordering.py` exists to prevent, reintroduced one layer lower.

A queue per connection with a single pump keeps delivery ordered per socket and
keeps one slow client's problem to itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from core.errors import RealtimeError, SlowConsumer


class Connection:
    """A WebSocket, a bounded outbound queue, and a reason it stopped."""

    def __init__(self, websocket: WebSocket, *, queue_size: int) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._failure: RealtimeError | None = None
        self._failed = asyncio.Event()

    # -- Subscriber -------------------------------------------------------

    def enqueue(self, frame: dict[str, Any]) -> None:
        """Take a frame for delivery. Never blocks, never raises.

        The contract `service.subscriptions.Subscriber` requires, and the reason
        it requires it: this is called from inside the bus's fan-out loop, where
        an exception would interrupt delivery to every subscriber after this one
        in the set.

        Every outbound frame goes through here, including command
        acknowledgements, so that a `subscribed` reply cannot overtake a price
        that was already queued. A client that saw the price first would have
        good reason to discard it as belonging to a market it had not finished
        subscribing to.
        """
        if self._failed.is_set():
            return

        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Not an error to raise here — the caller is the fan-out. Recorded
            # instead, and the connection's supervisor acts on it.
            self.fail(SlowConsumer())

    # -- lifecycle --------------------------------------------------------

    def fail(self, error: RealtimeError) -> None:
        """Record why this connection must close. First reason wins.

        First rather than last, because the first is the cause and everything
        after it is a consequence: a socket dropped for an expired token will
        also stop draining its queue, and reporting `slow_consumer` to a client
        whose real problem is an expired session would send it to debug its
        network instead of refreshing its token.
        """
        if self._failed.is_set():
            return

        self._failure = error
        self._failed.set()

    async def wait_failed(self) -> RealtimeError:
        """Block until something fails this connection, then return the reason."""
        await self._failed.wait()
        assert self._failure is not None  # set before the event, always
        return self._failure

    async def pump(self) -> None:
        """Deliver queued frames in order, until cancelled or the socket dies."""
        while True:
            frame = await self._queue.get()
            await self._websocket.send_json(frame)

    # -- introspection, for tests -----------------------------------------

    @property
    def pending(self) -> int:
        return self._queue.qsize()
