"""`ledger.orders`, as the database actually builds it. [T-6] #26

ADR 0024, "Resting orders fill against the AMM inside the trade that moved the
price". Asserted against Postgres, for `test_book_schema.py`'s reason. Each
refusal is pinned to the constraint that made it, so a refusal from some other
rule does not pass for this one. Every refused row below is built to break
exactly one rule; Postgres names only the first failing CHECK in name order,
which is why `no_shares` is FILLED. Column types, nullability and every
constraint's definition are held by the catalog comparison in
`test_migrations.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import Order, OrderStatus
from unit_test.market_fixtures import market_with_outcomes
from unit_test.schema_fixtures import refusing_constraint

LIQUIDITY_B = Decimal("100.0000")
CANCELLED_AT = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)


def _order(market_id: uuid.UUID, outcome_id: uuid.UUID, **over) -> Order:
    """A buy of 100 shares at 0.6000, resting untouched: its budget is
    `limit × quantity` exactly and nothing has filled yet."""
    fields = {
        "user_id": uuid.uuid4(),
        "market_id": market_id,
        "outcome_id": outcome_id,
        "side": Side.BUY,
        "limit_price": Decimal("0.6000"),
        "quantity": Decimal("100.0000"),
        "filled_quantity": Decimal("0.0000"),
        "budget": Decimal("60.0000"),
        "filled_value": Decimal("0.0000"),
        "status": OrderStatus.OPEN,
        "book_state_version": 0,
        "idempotency_key": f"order:{uuid.uuid4()}",
        "cancelled_at": None,
    }
    fields.update(over)
    return Order(**fields)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        pytest.param(
            {"limit_price": Decimal("0.0000")},
            "ck_orders_limit_price_between_0_and_1",
            id="limit_price_0",
        ),
        # A budget of 100, because the helper's 60 would also break
        # ck_orders_budget_covers_the_limit, which sorts first.
        pytest.param(
            {"limit_price": Decimal("1.0000"), "budget": Decimal("100.0000")},
            "ck_orders_limit_price_between_0_and_1",
            id="limit_price_1",
        ),
        # FILLED, because an OPEN order with nothing to fill also breaks
        # ck_orders_filled_iff_nothing_remains, which sorts first.
        pytest.param(
            {"quantity": Decimal("0.0000"), "status": OrderStatus.FILLED},
            "ck_orders_quantity_positive",
            id="no_shares",
        ),
        pytest.param(
            {"filled_quantity": Decimal("-0.0001")},
            "ck_orders_filled_quantity_nonneg",
            id="filled_negative",
        ),
        pytest.param(
            {"filled_quantity": Decimal("100.0001")},
            "ck_orders_filled_quantity_within_quantity",
            id="filled_past_quantity",
        ),
        pytest.param(
            {"budget": None},
            "ck_orders_budget_iff_buy",
            id="buy_without_budget",
        ),
        pytest.param(
            {"side": Side.SELL, "budget": Decimal("60.0000")},
            "ck_orders_budget_iff_buy",
            id="sell_with_budget",
        ),
        # 0.6000 × 33.3333 is 19.99998, so a budget one tick under the
        # rounded-up 20.0000 is short of the limit by a fraction of a tick.
        pytest.param(
            {"quantity": Decimal("33.3333"), "budget": Decimal("19.9999")},
            "ck_orders_budget_covers_the_limit",
            id="budget_rounded_down",
        ),
        pytest.param(
            {"filled_value": Decimal("-0.0001")},
            "ck_orders_filled_value_nonneg",
            id="filled_value_negative",
        ),
        pytest.param(
            {"filled_value": Decimal("60.0001")},
            "ck_orders_filled_value_within_budget",
            id="filled_value_past_budget",
        ),
        pytest.param(
            {"filled_quantity": Decimal("100.0000")},
            "ck_orders_filled_iff_nothing_remains",
            id="open_with_nothing_remaining",
        ),
        pytest.param(
            {"status": OrderStatus.FILLED, "filled_quantity": Decimal("20.5465")},
            "ck_orders_filled_iff_nothing_remains",
            id="filled_with_some_remaining",
        ),
        pytest.param(
            {"status": OrderStatus.CANCELLED},
            "ck_orders_cancelled_at_iff_cancelled",
            id="cancelled_without_a_time",
        ),
        pytest.param(
            {"cancelled_at": CANCELLED_AT},
            "ck_orders_cancelled_at_iff_cancelled",
            id="a_time_without_a_cancel",
        ),
    ],
)
async def test_a_row_breaking_one_rule_is_refused_by_that_rule(
    session: AsyncSession, overrides: dict, constraint: str
) -> None:
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_order(market_id, outcome_ids[0], **overrides))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert refusing_constraint(refusal) == constraint
    await session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        # Each budget is limit × quantity rounded up to the tick, as a
        # placement stores it.
        pytest.param(
            {"limit_price": Decimal("0.0001"), "budget": Decimal("0.0100")},
            id="limit_one_tick_above_0",
        ),
        pytest.param(
            {"limit_price": Decimal("0.9999"), "budget": Decimal("99.9900")},
            id="limit_one_tick_below_1",
        ),
        pytest.param(
            {"quantity": Decimal("0.0001"), "budget": Decimal("0.0001")},
            id="quantity_one_tick",
        ),
        pytest.param({"filled_value": Decimal("60.0000")}, id="filled_value_equal_to_budget"),
        # 0.6000 × 33.3335 is 20.0001 exactly.
        pytest.param(
            {"quantity": Decimal("33.3335"), "budget": Decimal("20.0001")},
            id="budget_covering_the_limit_exactly",
        ),
        pytest.param(
            {"status": OrderStatus.FILLED, "filled_quantity": Decimal("100.0000")},
            id="filled_equal_to_quantity",
        ),
        pytest.param(
            {"status": OrderStatus.CANCELLED, "cancelled_at": CANCELLED_AT},
            id="cancelled_with_a_time",
        ),
    ],
)
async def test_a_row_on_the_edge_of_a_rule_is_accepted(
    session: AsyncSession, overrides: dict
) -> None:
    """The other side of each strict-or-not choice above, so `>` and `>=`
    cannot pass alike."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    order = _order(market_id, outcome_ids[0], **overrides)

    session.add(order)
    await session.flush()
    # Read the id before expiring: an expired attribute lazy-loads, and
    # under asyncio that raises MissingGreenlet instead of querying.
    order_id = order.id
    session.expire_all()

    stored = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()
    for column, value in overrides.items():
        assert getattr(stored, column) == value


async def test_an_order_naming_another_markets_outcome_is_refused(
    session: AsyncSession,
) -> None:
    """The outcome must be one of this market's. Both markets have books, so
    only the composite key can refuse it."""
    market_id, _ = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    _, other_outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_order(market_id, other_outcome_ids[0]))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert refusing_constraint(refusal) == "fk_orders_market_outcome"
    await session.rollback()


async def test_the_same_idempotency_key_twice_is_refused(
    session: AsyncSession,
) -> None:
    """A retry with the same key answers with the order it placed (ADR 0024,
    "A placement stores its own `book_state_version`, and a replay answers
    from it"), which needs the key to name one row."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    session.add(_order(market_id, outcome_ids[0], idempotency_key="order:retry"))
    await session.flush()

    session.add(_order(market_id, outcome_ids[0], idempotency_key="order:retry"))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert refusing_constraint(refusal) == "uq_orders_idempotency_key"
    await session.rollback()


async def test_the_database_numbers_orders_in_insertion_order(
    session: AsyncSession,
) -> None:
    """`seq` breaks a tie at one price (ADR 0024, "Price priority, then
    time"), so the database assigns it, from one sequence for both sides."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    buy = _order(market_id, outcome_ids[0])
    session.add(buy)
    await session.flush()
    sell = _order(market_id, outcome_ids[0], side=Side.SELL, budget=None)
    session.add(sell)
    await session.flush()

    rows = await session.execute(select(Order.id, Order.seq).where(Order.id.in_([buy.id, sell.id])))
    seq_of = {order_id: seq for order_id, seq in rows.all()}

    assert seq_of[sell.id] > seq_of[buy.id]


async def test_an_order_cannot_supply_its_own_seq(session: AsyncSession) -> None:
    """GENERATED ALWAYS, not BY DEFAULT: an order row is inserted under the
    book lock, so `seq` is the lock order, and a writer that chose its own
    number could put an order ahead of one placed before it."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_order(market_id, outcome_ids[0], seq=1))

    with pytest.raises(DBAPIError) as refusal:
        await session.flush()

    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's
    # GeneratedAlwaysError, which carries the SQLSTATE.
    assert refusal.value.orig.__cause__.sqlstate == "428C9"
    await session.rollback()
