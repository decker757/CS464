"""The refresh against readers and against another refresh. [L-1] #38, [L-3] #40.

DECISIONS.md, "The leaderboard is stored as generations, and a refresh swaps
the whole ranking in one commit" and "A refresh writes under a
transaction-level advisory lock, and writes nothing if a generation at least
as new exists". Nothing here waits on time: a refresh is paused at its own
commit (`leaderboard_fixtures.CommitPause`), and a queued refresh is seen in
`pg_stat_activity`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.leaderboard_fixtures import (
    CommitPause,
    Directory,
    at,
    fixed_clock,
    generations,
    granted,
    newest,
    pausing_factory,
    queued_on_the_advisory_lock,
    refresh_with,
)
from unit_test.portfolio_fixtures import market_at
from unit_test.sell_fixtures import hold
from unit_test.settlement_fixtures import finish, raise_any, reached


async def _three_holders(session: AsyncSession, directory: Directory):
    """Three named holders, one of them holding in a market. Returns the
    market and the holders."""
    market = await market_at(session)
    holders = [
        await granted(session, directory, name)
        for name in ("first_holder", "second_holder", "third_holder")
    ]
    await hold(session, market, user_id=holders[0])
    return market, holders


async def test_a_reader_during_an_uncommitted_refresh_sees_the_whole_previous_generation(
    session: AsyncSession,
) -> None:
    """[L-3] #40: "Users never see partially calculated rankings."

    The second refresh is paused at its write commit, with its generation and
    entries in its own transaction. Its own session sees the new ranking
    whole, so the write has happened. A reader in another session sees the
    previous ranking, whole. At every commit the refresh makes, the reader's
    newest generation holds all of its entries.

    Catches a generation committed before its entries, a prune committed
    before the insert (an empty or missing board), and ranks updated in place.
    """
    directory = Directory()
    market, holders = await _three_holders(session, directory)
    await refresh_with(directory, clock=fixed_clock(at(0)))
    [previous] = await generations()
    await hold(session, market, user_id=holders[1], outcome=1)
    await session.commit()

    pause = CommitPause()
    task = asyncio.create_task(
        refresh_with(directory, clock=fixed_clock(at(60)), factory=pausing_factory(pause))
    )
    try:
        await reached(pause.reached, "the refresh never reached its write commit", [task])
        own = pause.own_view
        seen_meanwhile = await generations()
    finally:
        outcomes = await finish([task], pause.release)
    raise_any(outcomes)

    assert own.computed_at == at(60)
    assert own.ranked_users == 3 and own.is_whole
    assert own.by_user() != previous.by_user(), "the two rankings are the same"
    assert seen_meanwhile == [previous]
    assert all(view is None or view.is_whole for view in pause.reader_views)
    current = await newest()
    assert current.id == own.id and current.is_whole


@pytest.mark.parametrize(
    "stamp",
    [at(60), at(30)],
    ids=["same-instant-as-the-newest", "between-the-two"],
)
async def test_a_refresh_no_newer_than_the_newest_generation_inserts_nothing(
    session: AsyncSession, stamp: datetime
) -> None:
    """"If one does [have `computed_at >= its own`], it inserts nothing,
    deletes nothing, commits". The same instant catches `>` written for
    `>=`. An instant between the two kept generations catches a guard that
    compares with the oldest, and a prune that runs before the guard."""
    directory = Directory()
    market, holders = await _three_holders(session, directory)
    await refresh_with(directory, clock=fixed_clock(at(0)))
    await hold(session, market, user_id=holders[1], outcome=1)
    await refresh_with(directory, clock=fixed_clock(at(60)))
    before = await generations()
    assert [generation.computed_at for generation in before] == [at(0), at(60)]

    await refresh_with(directory, clock=fixed_clock(stamp))

    assert await generations() == before


async def test_an_older_refresh_queued_behind_a_newer_one_inserts_nothing(
    session: AsyncSession,
) -> None:
    """The race the advisory lock exists for: "Without this guard the slower,
    older one commits last."

    The newer refresh is paused at its write commit, holding the lock. The
    older one then runs, and must queue on the lock (seen in
    `pg_stat_activity`), wake after the newer one commits, see it, and write
    nothing.

    With the lock removed, or taken after the guard's read (ADR 0015: the
    read that decides the write comes after the lock), the older refresh's
    guard runs while the newer generation is uncommitted, finds nothing, and
    commits a generation stamped before the newer one. The first assertion
    then fails on that generation, not on a timeout.
    """
    directory = Directory()
    await _three_holders(session, directory)
    await session.commit()
    newer = CommitPause()
    older = CommitPause(pause=False)
    tasks = [
        asyncio.create_task(
            refresh_with(directory, clock=fixed_clock(at(60)), factory=pausing_factory(newer))
        )
    ]
    queued = False
    try:
        await reached(newer.reached, "the newer refresh never reached its write commit", tasks)
        tasks.append(
            asyncio.create_task(
                refresh_with(
                    directory, clock=fixed_clock(at(0)), factory=pausing_factory(older)
                )
            )
        )
        queued = await queued_on_the_advisory_lock(
            tasks[1], "the older refresh neither queued on the lock nor finished"
        )
    finally:
        outcomes = await finish(tasks, newer.release)
    raise_any(outcomes)

    every = await generations()
    assert [generation.computed_at for generation in every] == [at(60)], (
        "the older refresh wrote its generation after the newer one committed"
    )
    assert every[0].id == newer.own_view.id and every[0].is_whole
    assert queued, "the older refresh never waited on the advisory lock"
