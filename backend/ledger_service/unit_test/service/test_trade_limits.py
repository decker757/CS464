"""A trade decided by its own price limit. #640

A buy names the most it will pay and a sell the least it will accept, and the
limit replaces the exact-quote rule: a trade within its limit executes at the
price the book holds when the lock is taken, whatever `state_version` it
quoted. A trade that names no limit keeps the strict rule — the expand step
of the rollout, so the frontend that sends only `state_version` still works.

The comparison itself is `test_pricing.py`'s; these drive it through
`service/trading.py`. The race is `test_trade_concurrency.py`'s.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import PROCEEDS, TICK, hold, open_at_zero, sell
from unit_test.trade_fixtures import (
    B,
    Q,
    QUANTITY,
    Upstream,
    balance_of_user,
    book_row,
    buy,
    errors,
    expected_total,
    fund,
    ledger_counts,
    pricing,
    q_of,
    session_factory,
    trading,
    transaction_count,
    warm,
)

# Far above anything `QUANTITY` can cost against `Q`, so only the limit's
# presence is under test, not its value.
GENEROUS = Decimal("1e6")


# =========================================================================
# A buy
# =========================================================================
async def test_a_buy_within_its_limit_executes_at_the_current_price_after_the_book_moved(
    session: AsyncSession,
) -> None:
    """Quoted at version 0, executed at version 1's price: the limit, not
    the quote, decides, and the charge is what the book costs now."""
    upstream = Upstream()
    first_trader = uuid.uuid4()
    second_trader = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, first_trader)
    await fund(session, second_trader)

    first = await buy(session, upstream, user_id=first_trader, client_key="first")
    limit = -first.total * Decimal("1.05")

    second = await buy(
        session,
        upstream,
        user_id=second_trader,
        client_key="second",
        state_version=0,
        limit=limit,
    )

    assert second.total == expected_total([Q[0] + QUANTITY, Q[1]])
    assert -second.total <= limit
    assert second.total != first.total, (
        "the second buy was charged the first one's price, against a book the "
        "first had already moved"
    )
    assert second.state_version == 2


async def test_a_buy_beyond_its_limit_is_refused_with_the_current_total_and_nothing_moves(
    session: AsyncSession,
) -> None:
    """`409 price_beyond_limit`, carrying what the trade would cost now, and
    the rollback evidence: no transaction, no entry, no credit moved."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    credits = await fund(session, user_id)
    counts_before = await ledger_counts(session)
    limit = Decimal("0.0001")

    with pytest.raises(errors().PriceBeyondLimit) as raised:
        await buy(session, upstream, user_id=user_id, limit=limit)
    await session.rollback()

    current = pricing().trade_cost_of(
        Q, B, index=0, side=pricing().Side.BUY, quantity=QUANTITY
    )
    assert raised.value.current_total == current.magnitude
    assert raised.value.limit == limit
    assert raised.value.state_version == 0
    assert raised.value.code == "price_beyond_limit"

    # From a session of its own, which under READ COMMITTED sees only what
    # landed.
    async with session_factory()() as other:
        assert await ledger_counts(other) == counts_before
        assert await balance_of_user(other, user_id) == credits
        assert await q_of(other, upstream.market_id) == list(Q)
        assert (await book_row(other, upstream.market_id)).state_version == 0


async def test_a_buy_whose_price_fell_past_the_tolerance_still_executes(
    session: AsyncSession,
) -> None:
    """A limit is one-sided: a buy quoted at version 1 with no tolerance at
    all executes at version 2's lower price, because a sell in between moved
    the book in the buyer's favour."""
    upstream = Upstream()
    other_trader = uuid.uuid4()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, other_trader)
    await fund(session, user_id)

    await buy(session, upstream, user_id=other_trader, client_key="up")
    limit = -expected_total([Q[0] + QUANTITY, Q[1]])
    await buy(
        session,
        upstream,
        user_id=other_trader,
        client_key="down",
        side="sell",
        state_version=1,
    )

    trade = await buy(
        session, upstream, user_id=user_id, state_version=1, limit=limit
    )

    assert trade.total == expected_total(Q)
    assert -trade.total < limit


# =========================================================================
# A sell
# =========================================================================
@pytest.mark.parametrize(
    ("limit", "executes"),
    [(PROCEEDS - TICK, True), (PROCEEDS + TICK, False)],
    ids=["within", "beyond"],
)
async def test_a_sell_within_and_beyond_its_limit(
    session: AsyncSession, limit: Decimal, executes: bool
) -> None:
    """A sell's limit is a floor on its proceeds: one tick below what the
    book pays executes, one tick above is refused. A sell compared the buy's
    way round would get both wrong."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await hold(session, upstream, user_id=user_id)

    if executes:
        trade = await sell(session, upstream, user_id=user_id, limit=limit)
        assert trade.total == PROCEEDS
    else:
        with pytest.raises(errors().PriceBeyondLimit) as raised:
            await sell(session, upstream, user_id=user_id, limit=limit)
        assert raised.value.current_total == PROCEEDS
        assert raised.value.limit == limit


# =========================================================================
# The limit replaces the quote, and only when it is sent
# =========================================================================
@pytest.mark.parametrize("quoted", [7, None], ids=["newer-than-the-book", "absent"])
async def test_a_trade_with_a_limit_ignores_a_stale_state_version(
    session: AsyncSession, quoted: int | None
) -> None:
    """With a limit, `state_version` is not compared at all: a version the
    book has not reached, or none, executes at version 0's price."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(
        session, upstream, user_id=user_id, state_version=quoted, limit=GENEROUS
    )

    assert trade.total == expected_total(Q)
    assert trade.state_version == 1


async def test_a_trade_with_no_limit_still_refuses_a_stale_quote(
    session: AsyncSession,
) -> None:
    """The expand step's promise: a client that sends only `state_version`
    is held to strict equality exactly as before."""
    upstream = Upstream()
    mover = uuid.uuid4()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, mover)
    await fund(session, user_id)
    await buy(session, upstream, user_id=mover, client_key="mover")

    with pytest.raises(errors().QuoteStale) as raised:
        await buy(session, upstream, user_id=user_id, state_version=0)
    await session.rollback()

    assert raised.value.quoted == 0
    assert raised.value.current == 1


async def test_a_trade_naming_neither_a_limit_nor_a_state_version_is_refused_first(
    session: AsyncSession,
) -> None:
    """`execute` refuses it itself, before any lookup or call out, rather than
    trusting the route's validator to be its only caller."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    counts_before = await ledger_counts(session)

    with pytest.raises(ValueError, match="a limit or a state_version"):
        await buy(session, upstream, user_id=user_id, state_version=None, limit=None)

    assert upstream.calls == 0
    assert await ledger_counts(session) == counts_before


# =========================================================================
# A replay
# =========================================================================
async def test_a_replay_answers_the_stored_trade_whatever_limit_the_retry_carries(
    session: AsyncSession,
) -> None:
    """A retry is answered from the stored transaction before any price is
    read, so a limit the stored total is beyond cannot refuse a trade that
    has already happened."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    first = await buy(
        session, upstream, user_id=user_id, client_key="retry", limit=GENEROUS
    )
    assert -first.total > Decimal("0.0001")

    retry = await buy(
        session,
        upstream,
        user_id=user_id,
        client_key="retry",
        limit=Decimal("0.0001"),
    )

    assert retry == first
    key = trading().trade_key(user_id, upstream.market_id, "retry")
    assert await transaction_count(session, key=key) == 1
