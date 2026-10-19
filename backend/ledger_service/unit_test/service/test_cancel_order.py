"""Cancelling a limit order. [T-8] #28, ADR 0024.

Business rules, driven through `service/cancellation.py::cancel` with no HTTP.
The route is `unit_test/controller/test_order_routes.py`'s; a cancel racing a
fill is `test_cancel_order_concurrency.py`'s.

The figures are the placement suite's, worked there outside the code: a
resting buy of 13.3333 at 0.4321 holds 5.7614; the contract's example buy of
100 at 0.6000 fills 20.5465 on arrival for 11.8152 and rests holding 48.1848;
and a sell of 30 at 0.6000 against a holding of 54.3333 fills 13.7867 and holds
back 16.2133.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    OrderAlreadyCancelled,
    OrderAlreadyFilled,
    OrderNotFound,
    OrderReleased,
)
from model.entities import OrderStatus
from service import cancellation, ledger_service, reservations
from unit_test.placement_fixtures import (
    limit_upstream,
    open_at,
    order_row,
    place,
    record_result,
)
from unit_test.sell_fixtures import hold
from unit_test.service.test_place_order import (
    EXAMPLE_COST,
    EXAMPLE_FILL,
    EXAMPLE_LIMIT,
    EXAMPLE_QUANTITY,
    HELD,
    RESTING_BUDGET,
    RESTING_LIMIT,
    RESTING_QUANTITY,
    SMALL_QUANTITY,
    ZERO_Q,
    _example_market,
    _funded,
)
from unit_test.trade_fixtures import (
    assert_ledger_balances,
    balance_of_user,
    position_of,
    session_factory,
    transaction_count,
)

NOTHING = Decimal("0.0000")


async def _committed(order_id: uuid.UUID, user_id: uuid.UUID):
    """The order row and the owner's balance, from a session that sees only
    what was committed."""
    async with session_factory()() as other:
        row = await order_row(other, order_id)
        balance = await ledger_service.balance_of_user(other, user_id)
    return row, balance


# =========================================================================
# What a cancel releases
# =========================================================================
async def test_cancelling_a_resting_buy_releases_its_whole_budget_and_writes_no_entry(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """#28: "Cancel releases reserved credits ... for the unfilled remainder".
    Nothing has filled, so the remainder is the whole budget, and the balance
    route's `available` is the balance again. A reservation is not an entry,
    so the release is not one either (ADR 0024)."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    placed = await place(
        session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )
    transactions = await transaction_count(session)

    cancelled = await cancellation.cancel(session, placed.order.id, user_id=user_id)

    assert (cancelled.released_credits, cancelled.released_shares) == (RESTING_BUDGET, None)
    assert cancelled.order.status == "cancelled"
    assert cancelled.order.reserved_credits == NOTHING
    row, balance = await _committed(placed.order.id, user_id)
    assert row.status is OrderStatus.CANCELLED
    assert row.cancelled_at is not None
    assert cancelled.order.cancelled_at == row.cancelled_at
    assert (balance.amount, balance.reserved, balance.available) == (
        starting_credits,
        NOTHING,
        starting_credits,
    )
    assert await transaction_count(session) == transactions


async def test_cancelling_a_partly_filled_buy_releases_only_what_it_has_not_spent(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """The contract: "Fills stay: a cancel releases only the remainder". The
    example's order spent 11.8152 of its 60.0000 budget on arrival, so the
    cancel releases 48.1848, and the fill stays filled and paid for."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    placed = await place(
        session, upstream, user_id=user_id, limit_price=EXAMPLE_LIMIT, quantity=EXAMPLE_QUANTITY
    )
    transactions = await transaction_count(session)

    cancelled = await cancellation.cancel(session, placed.order.id, user_id=user_id)

    assert (cancelled.released_credits, cancelled.released_shares) == (
        Decimal("48.1848"),
        None,
    )
    assert cancelled.order.filled_quantity == EXAMPLE_FILL
    assert cancelled.order.average_fill_price == Decimal("0.5750")
    row, balance = await _committed(placed.order.id, user_id)
    assert (row.status, row.filled_quantity, row.filled_value) == (
        OrderStatus.CANCELLED,
        EXAMPLE_FILL,
        EXAMPLE_COST,
    )
    spent = starting_credits - EXAMPLE_COST
    assert (balance.amount, balance.reserved, balance.available) == (spent, NOTHING, spent)
    assert await transaction_count(session) == transactions
    await assert_ledger_balances(session)


async def test_cancelling_a_partly_filled_sell_releases_only_its_unsold_shares(
    session: AsyncSession,
) -> None:
    """#28: "... or shares". The sell sold 13.7867 of its 30 shares on
    arrival, so 16.2133 come back to the seller's unreserved holding, and the
    position itself does not move: the shares never left it."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    user_id = await _funded(session)
    await hold(session, upstream, user_id=user_id, quantity=HELD)
    placed = await place(
        session,
        upstream,
        user_id=user_id,
        side="sell",
        limit_price=EXAMPLE_LIMIT,
        quantity=Decimal("30.0000"),
    )
    balance = await balance_of_user(session, user_id)

    cancelled = await cancellation.cancel(session, placed.order.id, user_id=user_id)

    assert (cancelled.released_credits, cancelled.released_shares) == (
        None,
        Decimal("16.2133"),
    )
    assert cancelled.order.reserved_shares == NOTHING
    session.expire_all()
    assert await reservations.reserved_shares_of(
        session, user_id, market_id=upstream.market_id, outcome_id=upstream.outcomes[0]
    ) == Decimal(0)
    position = await position_of(session, user_id, upstream.market_id, upstream.outcomes[0])
    assert position.quantity == HELD - Decimal("13.7867")
    assert await balance_of_user(session, user_id) == balance


# =========================================================================
# What a cancel refuses
# =========================================================================
async def _ended(session: AsyncSession, ending: str) -> tuple[uuid.UUID, uuid.UUID]:
    """An order that has ended one way or another, and its owner."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    fills = ending.startswith("filled")
    placed = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT if fills else RESTING_LIMIT,
        quantity=SMALL_QUANTITY if fills else RESTING_QUANTITY,
    )
    if ending == "cancelled":
        await cancellation.cancel(session, placed.order.id, user_id=user_id)
    if ending.endswith("released"):
        await record_result(session, upstream)
    return placed.order.id, user_id


@pytest.mark.parametrize(
    ("ending", "refusal"),
    [
        ("filled", OrderAlreadyFilled),
        ("cancelled", OrderAlreadyCancelled),
        ("released", OrderReleased),
        ("filled_then_released", OrderAlreadyFilled),
    ],
    ids=["filled", "cancelled", "released", "filled_then_released"],
)
async def test_an_order_that_has_ended_is_refused_and_left_as_it_was(
    session: AsyncSession, ending: str, refusal: type[Exception]
) -> None:
    """The contract's three 409s. A second cancel is `order_already_cancelled`
    and changes nothing, so a retry cannot release twice. The status is read
    before the result: a filled order on a market with a result had nothing
    left for the result to release, so it is `order_already_filled`."""
    order_id, user_id = await _ended(session, ending)
    before, _ = await _committed(order_id, user_id)

    with pytest.raises(refusal):
        await cancellation.cancel(session, order_id, user_id=user_id)

    after, _ = await _committed(order_id, user_id)
    assert (after.status, after.cancelled_at, after.filled_quantity) == (
        before.status,
        before.cancelled_at,
        before.filled_quantity,
    )


async def test_another_traders_order_is_order_not_found_and_stays_open(
    session: AsyncSession,
) -> None:
    """The contract: "No such order, or not yours. The two look the same on
    purpose." Only the owner may cancel, whatever the caller's role."""
    upstream = await _example_market(session)
    owner = await _funded(session)
    placed = await place(
        session, upstream, user_id=owner, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )

    with pytest.raises(OrderNotFound):
        await cancellation.cancel(session, placed.order.id, user_id=uuid.uuid4())

    row, balance = await _committed(placed.order.id, owner)
    assert (row.status, row.cancelled_at) == (OrderStatus.OPEN, None)
    assert balance.reserved == RESTING_BUDGET
