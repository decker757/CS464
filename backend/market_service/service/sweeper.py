"""The background task that writes CLOSED once a market's close time passes. [F-4] #44.

Deliberately thin and not the authority; `service/closing.py` decides. This
owns the timer, the session, and the promise that a database error does not
stop markets closing. Why a sweep rather than Redis or cron, and what it
costs, is ADR 0011. A per-market in-process timer would die with a deploy and
need re-arming by a sweep at boot anyway.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from service import closing

logger = logging.getLogger(__name__)

# A backlog drains in back-to-back batches, not one per tick. Each batch
# closes its rows for good, so a correct drain always ends. Hitting this cap
# means either a real backlog, which the next tick carries on with, or a
# market that is not staying closed; the warning below says so.
MAX_DRAIN_BATCHES = 50


async def run_close_sweeper(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float,
    batch_limit: int,
) -> None:
    """Close due markets every `interval_seconds`, forever, starting at once.

    Started and cancelled by `main.py`. Swallows every `Exception`, because a
    task that ended is a service where no market ever closes again and nothing
    says so; `CancelledError` is a `BaseException`, so shutdown still works.
    ADR 0011.
    """
    logger.info(
        "close sweeper started: every %.1fs, up to %d markets per batch",
        interval_seconds,
        batch_limit,
    )
    while True:
        try:
            await _drain(session_factory, batch_limit)
        except Exception:
            logger.exception("close sweep failed; retrying in %.1fs", interval_seconds)
        await asyncio.sleep(interval_seconds)


async def _drain(
    session_factory: async_sessionmaker[AsyncSession], batch_limit: int
) -> int:
    """Close due markets in batches until one comes back short. Returns the total.

    One session for the whole drain, one transaction per batch: each batch's
    commit releases its row locks, and reopening the session between batches
    would only churn the pool the autosave also draws from.
    """
    total = 0
    async with session_factory() as session:
        for _ in range(MAX_DRAIN_BATCHES):
            closed = await closing.close_due_markets(session, limit=batch_limit)

            total += len(closed)
            if closed:
                # Silent when nothing was due, which is almost always.
                logger.info(
                    "closed %d market(s) at their closing time: %s", len(closed), closed
                )

            if len(closed) < batch_limit:
                return total

    logger.warning(
        "close sweep stopped after %d full batches; either a real backlog or a "
        "market that is not staying closed",
        MAX_DRAIN_BATCHES,
    )
    return total
