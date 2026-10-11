"""Waiting on another session in a race test. ADR 0015.

Shared by `test_sessions.py` and `test_suspensions.py`: both force an order a
Barrier alone cannot, by holding one transaction open and waiting until the
other is queued behind it.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def wait_briefly(event: asyncio.Event) -> None:
    """Wait for the other party, but never long enough to hang a fixed build."""
    with suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=1)


async def wait_until_blocked_on_a_lock(session: AsyncSession, pid: int) -> None:
    """Poll until backend `pid` waits on a lock, for at most two seconds.

    `pg_blocking_pids` reads the lock table live; `pg_stat_activity` would not,
    because it is snapshotted once per transaction.
    """
    stmt = text("SELECT cardinality(pg_blocking_pids(:pid)) > 0")
    for _ in range(200):
        if await session.scalar(stmt, {"pid": pid}):
            return
        await asyncio.sleep(0.01)
    # Carrying on would test the two sessions one after the other, not the race.
    pytest.fail(f"backend {pid} never blocked on a lock")
