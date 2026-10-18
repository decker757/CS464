"""`reservations.reserved_of`: what a user's live buy orders hold back. [T-6] #26

ADR 0024, "A reservation is derived from the order rows, and it is
`posting`'s floor", and the D-NEW "An open order is live only while its market
has no result". Each case below stores one order that counts beside one that
must not, so a missing filter shows as a wrong sum rather than hiding behind a
single row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import MarketResult, ResultKind
from service import reservations
from unit_test.market_fixtures import market_with_outcomes
from unit_test.order_fixtures import add_order

LIQUIDITY_B = Decimal("100.0000")

# Budget less what has filled, at values that do not round to each other.
COUNTED_BUDGET = "48.1848"
COUNTED_FILLED_VALUE = "12.3457"
COUNTED = Decimal("35.8391")


async def _settle(session: AsyncSession, market_id: uuid.UUID, outcome_id: uuid.UUID) -> None:
    session.add(
        MarketResult(
            market_id=market_id,
            kind=ResultKind.SETTLED,
            outcome_id=outcome_id,
            recorded_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def test_a_user_with_no_orders_reserves_nothing(session: AsyncSession) -> None:
    assert await reservations.reserved_of(session, uuid.uuid4()) == Decimal(0)


@pytest.mark.parametrize(
    "excluded",
    [
        "cancelled",
        # Filled for less than its budget: the 10 left over is released with
        # the order, not still held (ADR 0024's first worked case).
        "filled_under_budget",
        # A sell reserves shares, not credits, even once it has proceeds.
        "sell",
        "another_users",
        # Still `open` in its status column, and released by the result row.
        "settled_market",
    ],
)
async def test_only_the_users_live_buy_orders_reserve_credits(
    session: AsyncSession, excluded: str
) -> None:
    """The counted order is in one market and the excluded one in another, so
    the sum spans markets and a result row releases only its own market."""
    user_id = uuid.uuid4()
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    other_market_id, other_outcome_ids = await market_with_outcomes(
        session, liquidity_b=LIQUIDITY_B
    )
    await add_order(
        session,
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_ids[0],
        budget=COUNTED_BUDGET,
        filled_value=COUNTED_FILLED_VALUE,
    )

    elsewhere = {"market_id": other_market_id, "outcome_id": other_outcome_ids[0]}
    if excluded == "cancelled":
        await add_order(
            session, user_id=user_id, budget="20.0000", status="cancelled", **elsewhere
        )
    elif excluded == "filled_under_budget":
        await add_order(
            session,
            user_id=user_id,
            budget="60.0000",
            filled_value="50.0000",
            status="filled",
            **elsewhere,
        )
    elif excluded == "sell":
        await add_order(
            session, user_id=user_id, budget=None, filled_value="0.4000", **elsewhere
        )
    elif excluded == "another_users":
        await add_order(session, user_id=uuid.uuid4(), budget="20.0000", **elsewhere)
    else:
        await add_order(session, user_id=user_id, budget="20.0000", **elsewhere)
        await _settle(session, other_market_id, other_outcome_ids[0])

    assert await reservations.reserved_of(session, user_id) == COUNTED


async def test_every_live_buy_order_adds_its_own_remainder(
    session: AsyncSession,
) -> None:
    """Two live buys in two markets: the reservation is both remainders, not
    the larger or the latest."""
    user_id = uuid.uuid4()
    for budget, filled_value in [(COUNTED_BUDGET, COUNTED_FILLED_VALUE), ("30.0001", "0.0000")]:
        market_id, outcome_ids = await market_with_outcomes(
            session, liquidity_b=LIQUIDITY_B
        )
        await add_order(
            session,
            user_id=user_id,
            market_id=market_id,
            outcome_id=outcome_ids[1],
            budget=budget,
            filled_value=filled_value,
        )

    assert await reservations.reserved_of(session, user_id) == COUNTED + Decimal(
        "30.0001"
    )


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        ("open_sell", Decimal("1.5000")),
        ("cancelled_sell", Decimal("0.5000")),
        ("filled_sell", Decimal("0.5000")),
        ("buy", Decimal("0.5000")),
        ("another_users_sell", Decimal("0.5000")),
        ("sell_of_the_other_outcome", Decimal("0.5000")),
        ("sell_in_another_market", Decimal("0.5000")),
        # Every order on the market is released by its result row.
        ("settled_market", Decimal("0.0000")),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_reserved_shares_are_the_users_live_sells_of_that_outcome(
    session: AsyncSession, other: str, expected: Decimal
) -> None:
    """ADR 0024: a sell reserves `quantity - filled_quantity` of its outcome
    while it is live. One counted sell, half filled, holds 0.5000; a second
    order beside it in the same market and outcome counts only if it is
    another live sell of this user's, so a missing filter shows as a wrong
    sum."""
    user_id = uuid.uuid4()
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    other_market_id, other_outcome_ids = await market_with_outcomes(
        session, liquidity_b=LIQUIDITY_B
    )
    here = {"market_id": market_id, "outcome_id": outcome_ids[0]}
    await add_order(session, user_id=user_id, budget=None, filled_value="0.4000", **here)

    owner = uuid.uuid4() if other == "another_users_sell" else user_id
    where = here
    if other == "sell_of_the_other_outcome":
        where = {"market_id": market_id, "outcome_id": outcome_ids[1]}
    elif other == "sell_in_another_market":
        where = {"market_id": other_market_id, "outcome_id": other_outcome_ids[0]}
    status = {"cancelled_sell": "cancelled", "filled_sell": "filled"}.get(other, "open")
    budget = "1.0000" if other == "buy" else None
    await add_order(session, user_id=owner, budget=budget, status=status, **where)
    if other == "settled_market":
        await _settle(session, market_id, outcome_ids[0])

    reserved = await reservations.reserved_shares_of(
        session, user_id, market_id=market_id, outcome_id=outcome_ids[0]
    )

    assert reserved == expected
