"""How many trades may hold a database connection at once, per market and in
all. #667

DECISIONS.md: "Only a few trades per market hold a ledger connection at
once". A trade waiting for a book lock holds its connection while it waits,
so on one hot market the waiting trades could take every connection this
process has. These limits make the rest wait here, in memory, holding none.

Pure and in-process: semaphores on one event loop. `service/connection_slots.py`
holds the one instance and sizes it from the settings. The book lock in
Postgres stays the only authority over a trade's correctness (ADR 0015); this
decides only who may hold a connection while they wait for it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class _MarketSlots:
    """One market's semaphore, and how many requests hold or wait for it."""

    def __init__(self, limit: int) -> None:
        self.semaphore = asyncio.Semaphore(limit)
        self.users = 0


class ConnectionSlots:
    """At most `per_market` holders per market, and `total` across markets.

    No timeout: a waiting request costs memory, not a connection, and the
    client's own timeout still applies. Not safe across threads; nothing
    between reading the map and counting a user awaits.
    """

    def __init__(self, *, per_market: int, total: int) -> None:
        self._per_market = per_market
        self._total = asyncio.Semaphore(total)
        self._by_market: dict[uuid.UUID, _MarketSlots] = {}

    def __len__(self) -> int:
        """How many markets have a request holding or waiting for a slot."""
        return len(self._by_market)

    @asynccontextmanager
    async def hold(self, market_id: uuid.UUID) -> AsyncIterator[None]:
        """Wait for a slot on `market_id`, then for one of the process's, and
        hold both until the block ends.

        The market's first, so a hot market's queue waits outside the
        process-wide limit and leaves it to the other markets. A market whose
        last holder or waiter leaves is forgotten, so the map stays as small
        as the markets in flight.
        """
        market = self._by_market.get(market_id)
        if market is None:
            market = _MarketSlots(self._per_market)
            self._by_market[market_id] = market
        market.users += 1
        try:
            async with market.semaphore, self._total:
                yield
        finally:
            market.users -= 1
            if market.users == 0:
                del self._by_market[market_id]
