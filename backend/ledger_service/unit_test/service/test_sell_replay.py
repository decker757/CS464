"""Retrying a sell. [T-3] #23, ADR 0017

The same ordering as a buy's retry, which `test_trade_replay.py` holds: the
idempotency lookup first and unlocked, then the gate, then the book lock and
the re-check under it. What is new here is that a sell is a second value of
`side`, so the replay comparison's `side` arm finally has something to
compare — a key that named a buy, resent as a sell of the same outcome and
quantity, is `409 idempotency_key_reused`.

Every holding is made by a real buy on a book opened at zero.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import (
    HELD,
    PROCEEDS,
    REMAINING_BASIS,
    REMAINING_QUANTITY,
    assert_wrote_nothing,
    footprint,
    hold,
    holding,
    open_at_zero,
    q_now,
    sell,
    version_of,
)
from unit_test.trade_fixtures import (
    Recorder,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    buy,
    errors,
    fund,
    trading,
    transaction_count,
)


async def _holder(session: AsyncSession):
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await hold(session, upstream, user_id=user_id)
    return upstream, user_id


async def test_a_retried_sell_returns_the_original_and_moves_everything_once(
    session: AsyncSession,
) -> None:
    """"Twice" means the position, `q` and `state_version` as well as the
    ledger rows. The retry quotes the newer version, as a client that
    re-previewed would, and is still the same trade."""
    upstream, user_id = await _holder(session)
    before = await balance_of_user(session, user_id)

    first = await sell(session, upstream, user_id=user_id, client_key="sell-once")
    again = await sell(
        session,
        upstream,
        user_id=user_id,
        client_key="sell-once",
        state_version=first.state_version,
    )

    assert again == first
    key = trading().trade_key(user_id, upstream.market_id, "sell-once")
    assert await transaction_count(session, key=key) == 1
    assert await q_now(session, upstream.market_id) == [REMAINING_QUANTITY, 0]
    assert await version_of(session, upstream.market_id) == 2
    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (REMAINING_QUANTITY, REMAINING_BASIS)
    assert await balance_of_user(session, user_id) == before + PROCEEDS
    await assert_ledger_balances(session)


async def test_a_buy_key_resent_as_a_sell_is_idempotency_key_reused(
    session: AsyncSession,
) -> None:
    """Same key, same outcome, same quantity, the other side. Answering it
    with the buy would tell a trader who asked to sell that they had bought.

    The trader holds enough to sell, so nothing but the comparison can refuse
    this."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await buy(
        session,
        upstream,
        user_id=user_id,
        quantity=HELD,
        state_version=0,
        client_key="one-key",
    )
    before = await footprint(session, upstream.market_id)

    with pytest.raises(errors().IdempotencyKeyReused):
        await sell(
            session, upstream, user_id=user_id, quantity=HELD, client_key="one-key"
        )

    await assert_wrote_nothing(session, upstream.market_id, before)


async def test_a_sell_retried_after_the_market_closed_returns_the_original_sell(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)
    first = await sell(session, upstream, user_id=user_id, client_key="lost")
    upstream.closes()
    calls = upstream.calls

    again = await sell(
        session,
        upstream,
        user_id=user_id,
        client_key="lost",
        state_version=first.state_version,
    )

    assert again == first
    assert again.total == PROCEEDS
    assert upstream.calls == calls, "the retry asked market_service anything"
    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (REMAINING_QUANTITY, REMAINING_BASIS)


async def test_a_retried_sell_publishes_nothing(session: AsyncSession) -> None:
    upstream, user_id = await _holder(session)
    first = await sell(session, upstream, user_id=user_id, client_key="once")
    recorder = Recorder()

    await sell(
        session,
        upstream,
        user_id=user_id,
        client_key="once",
        state_version=first.state_version,
        redis_client=recorder,
    )

    assert recorder.calls == []
