"""One client's socket and the bounded queue in front of it. [F-2] #42

The concrete `service.subscriptions.Subscriber`, in `controller` because a send
queue is transport. One queue and one pump per connection keeps delivery
ordered per socket and keeps a slow client's problem its own. ADR 0010.
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

    # -- lifecycle --------------------------------------------------------

    def fail(self, error: RealtimeError) -> None:
        """Record why this connection must close. The first reason wins.

        Today only `enqueue` calls this, with `SlowConsumer`; recording once
        means later overflows cannot replace the reason already given.
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

    # -- Subscriber -------------------------------------------------------

    def enqueue(self, frame: dict[str, Any]) -> None:
        """Take a frame for delivery. Never blocks, never raises.

        It runs inside the hub's fan-out loop. Acknowledgements go through here
        too, so a `subscribed` reply cannot overtake a price already queued.
        """
        if self._failed.is_set():
            return

        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Recorded, not raised: the caller is the fan-out.
            self.fail(SlowConsumer())

    # -- introspection, for tests -----------------------------------------

    @property
    def pending(self) -> int:
        """Frames queued and not yet sent."""
        return self._queue.qsize()
