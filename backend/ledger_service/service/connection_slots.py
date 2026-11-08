"""The slot a trade or a placement holds while it may hold a connection. #667

DECISIONS.md, "Only a few trades per market hold a ledger connection at
once". One `core.connection_slots.ConnectionSlots` per process, sized from the
settings the engine is sized from, so whatever the trades hold, at least
`database_connections_for_reads` of the pool stay free for reads and `/ready`.

The trade path and the placement take a slot before their first statement and
give it back after their transaction has ended: `AsyncSession` begins
lazily, so the first statement checks the connection out, and it stays out
until the commit or the rollback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.connection_slots import ConnectionSlots

_slots: ConnectionSlots | None = None


def _process_slots() -> ConnectionSlots:
    global _slots
    if _slots is None:
        settings = get_settings()
        _slots = ConnectionSlots(
            per_market=settings.database_connections_per_market,
            total=settings.trade_connections,
        )
    return _slots


@asynccontextmanager
async def hold_a_slot(session: AsyncSession, market_id: uuid.UUID) -> AsyncIterator[None]:
    """Wait, holding no connection, for one of `market_id`'s slots, and hold
    it until the block ends.

    Rolls `session` back on the way out, before the slot is given back:
    after a commit that is a no-op, and after a replay or a refusal it
    returns the connection the request would otherwise keep until its
    session closed, after the next trade had taken the slot. Enter it before
    `session` has issued a statement, or the connection is already out.
    """
    async with _process_slots().hold(market_id):
        try:
            yield
        finally:
            await session.rollback()
