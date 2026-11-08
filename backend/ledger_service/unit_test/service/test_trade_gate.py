"""The stopped-market gate on the trade path, answered from the copy. [F-8] #109, ADR 0017, ADR 0030.

`local_copy.ensure_trading` reads the stored status and close time the copy
holds, so a market an administrator closed and one the clock closed are both
refused, and neither costs a call. `test_local_copy.py` owns the predicate
over hand-built terms and the miss path's refresh; this file drives the gate
through `trading.execute`, against a book that exists, and asks what it
refuses, what it sends to Postgres and what it writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import loaded_copy
from unit_test.trade_fixtures import (
    FUTURE,
    Upstream,
    book_row,
    buy,
    capture_sql,
    entities,
    errors,
    fresh_copy,
    fund,
    locks,
    session_factory,
    warm,
    writes,
)


def _local_copy():
    """`service/local_copy.py`, imported inside each test so a missing name
    fails one test rather than collection (D-007)."""
    from service import local_copy  # noqa: PLC0415

    return local_copy


async def _warm_and_funded(session: AsyncSession, upstream: Upstream) -> uuid.UUID:
    """A market weeks into trading and a trader holding their grant."""
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    return user_id


# =========================================================================
# Which markets are refused
# =========================================================================
@pytest.mark.parametrize("status", ["closed", "pending_resolution", "approved"])
async def test_a_market_that_is_not_open_is_refused(
    session: AsyncSession, status: str
) -> None:
    """The criterion's own three cases. ADR 0017's `409 market_closed`."""
    upstream = Upstream()
    user_id = await _warm_and_funded(session, upstream)
    upstream.closes(status=status)

    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=user_id)


@pytest.mark.parametrize("status", ["settled", "draft", "submitted", "halted"])
async def test_a_status_nobody_enumerated_is_refused_too(
    session: AsyncSession, status: str
) -> None:
    """"One comparison, not a list of statuses": a list-based gate agrees with
    `!= "open"` on the cases above and would accept these, `settled` first.
    """
    upstream = Upstream()
    user_id = await _warm_and_funded(session, upstream)
    upstream.closes(status=status)

    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=user_id)


async def test_an_open_market_is_allowed_through(session: AsyncSession) -> None:
    """The main flow: open, with a close time ahead, trades."""
    upstream = Upstream()
    user_id = await _warm_and_funded(session, upstream)

    trade = await buy(session, upstream, user_id=user_id)

    assert trade.state_version == 1


async def test_a_market_closed_early_with_a_future_close_time_is_refused(
    session: AsyncSession,
) -> None:
    """[2.3] #7: an early close leaves `close_time` in the future (ADR 0014),
    so a gate on `close_time` alone would accept these trades. Closed after
    the book was committed, as in production."""
    upstream = Upstream()
    user_id = await _warm_and_funded(session, upstream)

    upstream.closes()  # status moves, close_time deliberately left in the future

    assert upstream.body["close_time"] == FUTURE, (
        "an early close must not move close_time; ADR 0014 is the record"
    )
    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=user_id)


# =========================================================================
# What the gate touches: no query on a market the copy holds, no write, and
# no lock on the miss path's one read
# =========================================================================
async def test_an_open_gate_sends_nothing_to_postgres(session: AsyncSession) -> None:
    """A market the copy holds is answered in memory. Reading the book up
    front would pass every other test and cost a read on every trade."""
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _local_copy().ensure_trading(
            session, fresh_copy(upstream), upstream.market_id, now=datetime.now(UTC)
        )

    assert statements == [], (
        "the gate's only job on a market the copy holds is a lookup in memory; "
        f"it sent {len(statements)} statement(s) to Postgres:\n" + "\n".join(statements)
    )


async def test_a_refused_gate_writes_nothing(session: AsyncSession) -> None:
    """A refusal leaves the database untouched: the gate runs before the
    book's first touch and the grant, so a refused trade on a market nobody
    has touched opens no book and mints nothing."""
    upstream = Upstream()
    upstream.closes()

    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=uuid.uuid4())

    ents = entities()
    async with session_factory()() as observer:
        for table in (ents.MarketBook, ents.Account, ents.Transaction, ents.Entry):
            count = (await observer.execute(select(func.count()).select_from(table))).scalar_one()
            assert count == 0, f"a refused gate wrote to {table.__tablename__}"
    assert upstream.calls == 0


async def test_the_miss_path_s_book_read_is_unlocked(session: AsyncSession) -> None:
    """A market with a book that the copy does not hold is
    `market_terms_unavailable`, decided by reading the book. ADR 0015 governs
    reads that decide a write; this one decides an error code, so it takes no
    lock on the hottest row there is, and writes nothing."""
    upstream = Upstream()
    user_id = await _warm_and_funded(session, upstream)
    assert await book_row(session, upstream.market_id) is not None

    with capture_sql() as statements:
        with pytest.raises(errors().MarketTermsUnavailable):
            await buy(session, upstream, user_id=user_id, local_copy=loaded_copy({}))

    assert locks(statements) == [], (
        "the miss path reads the book to choose an error code, which decides "
        "no write and takes no lock:\n" + "\n".join(locks(statements))
    )
    assert writes(statements) == [], (
        "the miss path is a read:\n" + "\n".join(writes(statements))
    )
