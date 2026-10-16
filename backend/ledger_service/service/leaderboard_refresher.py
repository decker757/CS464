"""The background task that rebuilds the leaderboard on a timer. [L-3] #40.

Deliberately thin: `service/leaderboard.py::refresh` does the work and owns the
rules. This owns the timer and the promise that a failed refresh does not stop
the next one. Why the first refresh runs inside the task, why it sleeps after
each refresh rather than at a fixed rate, and what a failure leaves behind are
the DECISIONS.md entries that cite #40. ADR 0021.

Twin: `market_service/service/sweeper.py::run_close_sweeper`, the same loop
shape. Kept copied, not shared: ADR 0012's bar.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from service import leaderboard

logger = logging.getLogger(__name__)


async def run_leaderboard_refresher(
    session_factory: async_sessionmaker[AsyncSession],
    auth_client: httpx.AsyncClient,
    *,
    interval_seconds: float,
) -> None:
    """Refresh the ranking, wait `interval_seconds`, and again, forever.

    Started and cancelled by `main.py`; the first refresh is the first
    iteration. Swallows every `Exception`, because a task that ended is a board
    that never updates again and nothing says so; `CancelledError` is a
    `BaseException`, so shutdown still works. A failed refresh writes nothing,
    so the previous ranking stays. ADR 0021.
    """
    logger.info(
        "leaderboard refresher started: a refresh now, then every %.1fs",
        interval_seconds,
    )
    while True:
        try:
            await leaderboard.refresh(session_factory, auth_client)
        except Exception:
            logger.exception(
                "leaderboard refresh failed; retrying in %.1fs", interval_seconds
            )
        await asyncio.sleep(interval_seconds)
