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
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    Upstream,
    buy,
    errors,
    fund,
    trading,
    warm,
)

_KEY = "lost-reply"


async def _committed(session: AsyncSession, upstream: Upstream, user_id: uuid.UUID):
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


async def test_another_traders_key_on_the_same_market_is_trade_not_found(
    session: AsyncSession,
) -> None:
    upstream = Upstream()
    await _committed(session, upstream, uuid.uuid4())

    with pytest.raises(errors().TradeNotFound):
        await trading().get_by_key(
            session,
            user_id=uuid.uuid4(),
            market_id=upstream.market_id,
            client_key=_KEY,
        )


async def test_the_callers_key_on_another_market_is_trade_not_found(
    session: AsyncSession,
) -> None:
    """A client key is unique to one caller in one market, so the same string
    on another market may name another trade, or none."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await _committed(session, upstream, user_id)

    with pytest.raises(errors().TradeNotFound):
        await trading().get_by_key(
            session, user_id=user_id, market_id=uuid.uuid4(), client_key=_KEY
        )
