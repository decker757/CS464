"""The trade's context statement: what the locked section reads, in one
statement issued after the book lock. #641, ADR 0015.

`trading.read_trade_context` replaces the separate reads the trade made after
the lock (the outcomes, the pool account, the position, the key re-check, the
latch, the clock and the reserved shares), so each test here holds it to what
those reads answer for the same state. The rule that keeps it a separate
statement rather than part of the lock statement is READ COMMITTED's: a
`SELECT ... FOR UPDATE` re-fetches only the rows it locked after a wait, and
answers every other table from its pre-lock snapshot.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import strip_outcomes
from unit_test.placement_fixtures import place, reserved_shares_of
from unit_test.sell_fixtures import HELD, hold, sell
from unit_test.settlement_fixtures import WINNER, SettleUpstream, settle
from unit_test.settlement_fixtures import hold as hold_for_settlement
from unit_test.settlement_fixtures import warm as warm_for_settlement
from unit_test.trade_fixtures import (
    Q,
    SMALL_QUANTITY,
    Upstream,
    books,
    buy,
    capture_sql,
    errors,
    fund,
    trading,
    warm,
    writes,
)

# A limit no buy of outcome 0 reaches at `Q`, whose price there is about 0.83,
# and one no sell of it reaches: both orders rest.
_BUY_LIMIT_THAT_RESTS = Decimal("0.0100")
_SELL_LIMIT_THAT_RESTS = Decimal("0.9900")
_RESTING_SHARES = Decimal("3.0000")


async def _context_under_lock(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    outcome_id: uuid.UUID,
    key: str = "trade:nobody:nothing:unused",
):
    """The book locked, then the context read, as the trade path reads it."""
    book = await books().lock_book(session, market_id)
    context = await trading().read_trade_context(
        session, book, user_id=user_id, outcome_id=outcome_id, key=key
    )
    return book, context


async def test_the_context_matches_the_separate_reads_on_a_warm_market(
    session: AsyncSession,
) -> None:
    """On a market nobody holds or has ordered in: the outcomes in position
    order with their `q`, the book's pool account, no position, no key, no
    result, no open orders, nothing reserved, and the clock after the lock.

    The positions are stored in the reverse of the insertion order, so a
    statement that forgot its ORDER BY returns them in the wrong order. The
    clock is read some time after the transaction began, so `now()`, which is
    the transaction's start and predates any wait for the lock, is refused.
    """
    upstream = Upstream()
    first, second = upstream.outcomes
    upstream.body["outcomes"] = [
        {"id": str(first), "position": 1, "label": "Inserted first"},
        {"id": str(second), "position": 0, "label": "Inserted second"},
    ]
    await warm(session, upstream)
    user_id = uuid.uuid4()

    transaction_start = (await session.execute(select(func.now()))).scalar_one()
    await asyncio.sleep(0.05)
    book, context = await _context_under_lock(
        session, upstream.market_id, user_id=user_id, outcome_id=first
    )

    separate = await trading().list_outcomes(session, upstream.market_id)
    assert [o.outcome_id for o in context.outcomes] == [second, first]
    assert [o.outcome_id for o in context.outcomes] == [o.outcome_id for o in separate]
    assert [o.q for o in context.outcomes] == Q
    assert context.pool_account.id == book.pool_account_id
    assert context.position is None
    assert context.key_taken is False
    assert context.has_result is False
    assert context.has_open_orders is False
    assert context.reserved_shares == Decimal(0)
    assert context.db_now - transaction_start >= timedelta(milliseconds=50)


async def test_the_context_sees_the_key_the_position_and_an_open_order_after_a_trade_and_a_placement(
    session: AsyncSession,
) -> None:
    """After a buy, the buy's derived key is taken and its position is the
    row `find_position` reads. After another trader's resting order, the
    market has an open order: the check is the market's, not the trader's,
    because every order there is one the cascade may fill."""
    upstream = Upstream()
    await warm(session, upstream)
    trader, placer = uuid.uuid4(), uuid.uuid4()
    await fund(session, trader)
    await fund(session, placer)
    await buy(session, upstream, user_id=trader, quantity=SMALL_QUANTITY, client_key="bought")
    await place(
        session,
        upstream,
        user_id=placer,
        limit_price=_BUY_LIMIT_THAT_RESTS,
        quantity=_RESTING_SHARES,
    )
    outcome_id = upstream.outcomes[0]
    key = trading().trade_key(trader, upstream.market_id, "bought")

    _, context = await _context_under_lock(
        session, upstream.market_id, user_id=trader, outcome_id=outcome_id, key=key
    )

    separate = await trading().find_position(
        session, user_id=trader, market_id=upstream.market_id, outcome_id=outcome_id
    )
    assert separate is not None
    assert context.position is not None
    assert (context.position.quantity, context.position.cost_basis) == (
        separate.quantity,
        separate.cost_basis,
    )
    assert context.position.quantity == SMALL_QUANTITY
    assert context.key_taken is True
    assert context.has_open_orders is True


async def test_the_context_sees_a_result_after_settlement(session: AsyncSession) -> None:
    """The latch's input: a settled market's `market_results` row, as
    `books.find_result` reads it."""
    upstream = SettleUpstream()
    await warm_for_settlement(session, upstream)
    await hold_for_settlement(session, upstream, [(uuid.uuid4(), WINNER, SMALL_QUANTITY)])
    await settle(session, upstream)

    _, context = await _context_under_lock(
        session,
        upstream.market_id,
        user_id=uuid.uuid4(),
        outcome_id=upstream.outcomes[WINNER],
    )

    assert await books().find_result(session, upstream.market_id) is not None
    assert context.has_result is True


async def test_the_context_reads_zero_reserved_shares_when_the_user_has_no_orders(
    session: AsyncSession,
) -> None:
    """A holder with no orders of their own reserves nothing, as a number
    and not a null, even when another holder's live sell order rests in the
    same outcome: the reservation is this user's, in this outcome."""
    upstream = Upstream()
    await warm(session, upstream)
    holder, other = uuid.uuid4(), uuid.uuid4()
    for user_id in (holder, other):
        await fund(session, user_id)
        await hold(session, upstream, user_id=user_id)
    await place(
        session,
        upstream,
        user_id=other,
        side="sell",
        limit_price=_SELL_LIMIT_THAT_RESTS,
        quantity=_RESTING_SHARES,
    )

    _, context = await _context_under_lock(
        session, upstream.market_id, user_id=holder, outcome_id=upstream.outcomes[0]
    )

    assert context.reserved_shares == Decimal(0)
    assert context.position is not None
    assert context.position.quantity == HELD


async def test_the_context_reads_the_reserved_shares_of_a_live_sell_order(
    session: AsyncSession,
) -> None:
    """The shares the holder's own resting sell order holds back, the figure
    `reservations.reserved_shares_query_of` reads on its own, which the holding check
    subtracts from the position."""
    upstream = Upstream()
    await warm(session, upstream)
    holder = uuid.uuid4()
    await fund(session, holder)
    await hold(session, upstream, user_id=holder)
    await place(
        session,
        upstream,
        user_id=holder,
        side="sell",
        limit_price=_SELL_LIMIT_THAT_RESTS,
        quantity=_RESTING_SHARES,
    )
    outcome_id = upstream.outcomes[0]

    _, context = await _context_under_lock(
        session, upstream.market_id, user_id=holder, outcome_id=outcome_id
    )

    separate = await reserved_shares_of(
        session, holder, market_id=upstream.market_id, outcome_id=outcome_id
    )
    assert context.reserved_shares == separate == _RESTING_SHARES


def _is_the_open_orders_read(statement: str) -> bool:
    """`write_cascade`'s own read of the market's open orders, which the
    context statement does not replace: whole order rows, where the
    reservation's read is a sum over them."""
    return statement.lower().lstrip().startswith("select ledger.orders.")


async def test_the_context_is_one_statement_issued_after_the_lock(
    session: AsyncSession,
) -> None:
    """ADR 0015 and #641: on a sell, which reads everything the context
    carries, the trade path sends exactly one statement between the book's
    `FOR UPDATE` and its write phase, which `post_all` opens with the account
    locks. That statement is the context, issued after the lock statement
    returned and not folded into it.

    Split the context into two statements, or read the key, the latch, the
    clock, the position or the reservation on its own again, and this fails.
    """
    upstream = Upstream()
    await warm(session, upstream)
    holder = uuid.uuid4()
    await fund(session, holder)
    await hold(session, upstream, user_id=holder)

    with capture_sql() as statements:
        await sell(session, upstream, user_id=holder)

    lowered = [s.lower() for s in statements]
    lock_at = next(
        i for i, s in enumerate(lowered) if "for update" in s and "market_books" in s
    )
    write_phase_at = next(
        i
        for i, s in enumerate(lowered)
        if i > lock_at and ("for update" in s or writes([s]))
    )
    between = [
        s for s in lowered[lock_at + 1 : write_phase_at] if not _is_the_open_orders_read(s)
    ]
    assert len(between) == 1, (
        "the trade read more than its one context statement under the book "
        "lock:\n" + "\n---\n".join(between)
    )
    (context_statement,) = between
    for read in ("market_outcomes", "positions", "transactions", "market_results"):
        assert read in context_statement, f"the context statement does not read {read}"
    assert "clock_timestamp" in context_statement


async def test_a_book_with_no_outcome_rows_is_still_market_book_incomplete(
    session: AsyncSession,
) -> None:
    """A book whose outcome rows are gone gives the context statement no row
    to carry its other columns on, and that is `MarketBookIncomplete`, through
    `book_prices.refuse_unpriceable`, never an empty context or an
    `IndexError`."""
    upstream = Upstream()
    await warm(session, upstream)
    await strip_outcomes(session, upstream.market_id)

    with pytest.raises(errors().MarketBookIncomplete):
        await _context_under_lock(
            session,
            upstream.market_id,
            user_id=uuid.uuid4(),
            outcome_id=upstream.outcomes[0],
        )
