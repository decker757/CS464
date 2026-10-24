"""What a portfolio row says its owner's live sell orders hold back. #410,
ADR 0024

`quantity` is the whole position. The trade route refuses a sell above the
position less the shares the seller's live sell orders reserve, so a client
that offers `quantity` as the most it can sell offers a number the ledger
refuses. Each row therefore carries `reserved_shares`: the sum of
`quantity - filled_quantity` over the owner's live sells of that outcome.

Driven through `service/portfolio.py::portfolio_of_user`. That the figure is
read in the portfolio's one statement is `test_portfolio_reads.py`'s; the wire
is `unit_test/controller/test_portfolio_routes.py`'s.

The figures are the placement suite's, worked there outside the code: on a
b = 100 book opened at zero, 54.3333 of outcome 0 takes it to 0.6326, and a
sell of 30 at 0.6000 then fills 13.7867 on arrival and rests holding back the
other 16.2133.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from service import cancellation
from service.portfolio import Portfolio
from unit_test.placement_fixtures import limit_upstream, open_at, place, record_result
from unit_test.portfolio_fixtures import funded, read_portfolio
from unit_test.sell_fixtures import hold
from unit_test.service.test_place_order import EXAMPLE_LIMIT, HELD, ZERO_Q
from unit_test.trade_fixtures import Upstream

# The placement suite's marketable sell: 30 at 0.6000 fills 13.7867 and rests.
MARKETABLE_QUANTITY = Decimal("30.0000")
MARKETABLE_RESERVED = Decimal("16.2133")

# Limits far above outcome 0's price, and far below it, so these orders rest
# through every trade the tests make after them.
RESTING_SELL_LIMIT = Decimal("0.9000")
RESTING_BUY_LIMIT = Decimal("0.1000")

OUTCOME_ONE = Decimal("41.0000")


def _reserved(result: Portfolio, upstream: Upstream, outcome: int) -> str:
    """The row's reserved shares as a string, so the scale is checked too."""
    key = (upstream.market_id, upstream.outcomes[outcome])
    return str(result.reserved_shares[key])


async def test_a_position_carries_what_its_owners_live_sell_orders_hold_back(
    session: AsyncSession,
) -> None:
    """Outcome 0 counts the part-filled sell's 16.2133 and a resting sell's
    5, so 21.2133. Beside them, each order that must not count adds a figure
    of its own: a cancelled sell 7, a resting buy 10, and another trader's
    sell on outcome 1, 3. Outcome 1, which the owner holds and has no sell
    order on, is 0.0000 and not None: any figure but 21.2133 and 0.0000 names
    the filter that was dropped."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    owner, _ = await funded(session)
    other, _ = await funded(session)

    await hold(session, upstream, user_id=owner, quantity=HELD)
    marketable = await place(
        session,
        upstream,
        user_id=owner,
        side="sell",
        limit_price=EXAMPLE_LIMIT,
        quantity=MARKETABLE_QUANTITY,
    )
    assert marketable.order.reserved_shares == MARKETABLE_RESERVED
    await place(
        session,
        upstream,
        user_id=owner,
        side="sell",
        limit_price=RESTING_SELL_LIMIT,
        quantity=Decimal("5.0000"),
    )
    cancelled = await place(
        session,
        upstream,
        user_id=owner,
        side="sell",
        limit_price=RESTING_SELL_LIMIT,
        quantity=Decimal("7.0000"),
    )
    await cancellation.cancel(session, cancelled.order.id, user_id=owner)
    await place(
        session,
        upstream,
        user_id=owner,
        side="buy",
        limit_price=RESTING_BUY_LIMIT,
        quantity=Decimal("10.0000"),
    )

    await hold(session, upstream, user_id=other, quantity=OUTCOME_ONE, outcome=1)
    await hold(session, upstream, user_id=owner, quantity=OUTCOME_ONE, outcome=1)
    await place(
        session,
        upstream,
        user_id=other,
        outcome=1,
        side="sell",
        limit_price=RESTING_SELL_LIMIT,
        quantity=Decimal("3.0000"),
    )

    result = await read_portfolio(session, owner)

    assert _reserved(result, upstream, 0) == "21.2133"
    assert _reserved(result, upstream, 1) == "0.0000"
    # The reservation is not taken off the position: the shares are still held.
    assert [(p.outcome_position, p.quantity) for p in result.positions] == [
        (0, HELD - (MARKETABLE_QUANTITY - MARKETABLE_RESERVED)),
        (1, OUTCOME_ONE),
    ]


async def test_a_sell_order_on_a_market_with_a_result_holds_nothing_back(
    session: AsyncSession,
) -> None:
    """D-NEW "An open order is live only while its market has no result": the
    order's status still reads `open` once the result is recorded, and it is
    not live. Read once before the result, so the zero after it cannot come
    from an order the read never counted."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    owner, _ = await funded(session)
    await hold(session, upstream, user_id=owner, quantity=HELD)
    await place(
        session,
        upstream,
        user_id=owner,
        side="sell",
        limit_price=RESTING_SELL_LIMIT,
        quantity=Decimal("5.0000"),
    )

    before = await read_portfolio(session, owner)
    await record_result(session, upstream)
    after = await read_portfolio(session, owner)

    assert _reserved(before, upstream, 0) == "5.0000"
    assert _reserved(after, upstream, 0) == "0.0000"


async def test_a_user_with_no_positions_has_no_reserved_shares(
    session: AsyncSession,
) -> None:
    """The anchor row joined to no market carries no outcome, so it adds no
    key that a caller could mistake for a position."""
    result = await read_portfolio(session, uuid.uuid4())

    assert result.reserved_shares == {}
