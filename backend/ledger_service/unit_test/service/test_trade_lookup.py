"""Asking whether a key committed a trade. [T-2] #584

A trade whose reply was lost leaves the client holding a key and no answer.
The lookup is how it gets one without sending the trade again: the trade the
key committed, exactly as a retry would have replayed it, or
`TradeNotFound`.

**It can only ever find the caller's own trade.** The stored key is
`trade:<user_id>:<market_id>:<idempotency_key>`, and the lookup derives it
from the caller and the market it is given, so another trader's key on the
same market, or the caller's own key on another market, names a different
row.

**It waits for nothing.** While a trade is still running, holding the book
lock with its rows written and not yet committed, the lookup answers
`TradeNotFound` at once rather than queueing behind it (ledger-service.md:
"as of this read"). The test pauses a real trade at its commit; adding
`books.lock_book` or `with_for_update()` to the lookup turns it red on the
`lock_timeout`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    TIMEOUT,
    Upstream,
    buy,
    errors,
    fund,
    session_factory,
    trading,
    warm,
)

if TYPE_CHECKING:
    from service.trading import TradeResult

_KEY = "lost-reply"
_LOCK_TIMEOUT = "2s"


async def _committed(
    session: AsyncSession, upstream: Upstream, user_id: uuid.UUID
) -> TradeResult:
    await warm(session, upstream)
    await fund(session, user_id)
    return await buy(session, upstream, user_id=user_id, client_key=_KEY)


async def test_a_committed_key_answers_with_the_trade_a_retry_would_replay(
    session: AsyncSession,
) -> None:
    """Equality on the whole result, against both the original and a retry:
    the lookup and the replay must not be able to describe one trade two
    ways."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    original = await _committed(session, upstream, user_id)
    replayed = await buy(session, upstream, user_id=user_id, client_key=_KEY)

    found = await trading().get_by_key(
        session, user_id=user_id, market_id=upstream.market_id, client_key=_KEY
    )

    assert found == original == replayed


async def test_a_key_whose_trade_was_refused_is_trade_not_found(
    session: AsyncSession,
) -> None:
    """The answer the client is asking for when a trade did not go through:
    a refusal writes nothing, so nothing is found under its key."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    with pytest.raises(errors().QuoteStale):
        await buy(session, upstream, user_id=user_id, client_key=_KEY, state_version=7)
    await session.rollback()

    with pytest.raises(errors().TradeNotFound):
        await trading().get_by_key(
            session, user_id=user_id, market_id=upstream.market_id, client_key=_KEY
        )


@pytest.mark.parametrize("asked_as", ["another trader", "another market"])
async def test_the_key_asked_as_someone_else_is_trade_not_found(
    session: AsyncSession, asked_as: str
) -> None:
    """The same client key, asked by another trader on the same market or by
    the caller on another market, names a different stored key: each guards
    one part of it."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await _committed(session, upstream, user_id)
    if asked_as == "another trader":
        user_id = uuid.uuid4()
        market_id = upstream.market_id
    else:
        market_id = uuid.uuid4()

    with pytest.raises(errors().TradeNotFound):
        await trading().get_by_key(
            session, user_id=user_id, market_id=market_id, client_key=_KEY
        )


async def test_a_trade_still_running_is_not_found_and_the_lookup_does_not_wait_for_it(
    session: AsyncSession,
) -> None:
    """The trade runs in its own session and stops at its commit, after its
    rows are flushed and while it holds the book lock. The lookup answers
    from what has committed, so it is `TradeNotFound` under a short
    `lock_timeout`; once the trade commits, it finds it."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    about_to_commit = asyncio.Event()
    may_commit = asyncio.Event()

    async with session_factory()() as other:
        real_commit = other.commit

        async def commit_when_allowed() -> None:
            await other.flush()
            about_to_commit.set()
            await may_commit.wait()
            await real_commit()

        other.commit = commit_when_allowed  # type: ignore[method-assign]
        running = asyncio.create_task(
            buy(other, upstream, user_id=user_id, client_key=_KEY)
        )
        try:
            await asyncio.wait_for(about_to_commit.wait(), timeout=TIMEOUT)
            await session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
            with pytest.raises(errors().TradeNotFound):
                await asyncio.wait_for(
                    trading().get_by_key(
                        session,
                        user_id=user_id,
                        market_id=upstream.market_id,
                        client_key=_KEY,
                    ),
                    timeout=TIMEOUT,
                )
        finally:
            may_commit.set()
        committed = await asyncio.wait_for(running, timeout=TIMEOUT)
    await session.rollback()

    found = await trading().get_by_key(
        session, user_id=user_id, market_id=upstream.market_id, client_key=_KEY
    )

    assert found == committed
