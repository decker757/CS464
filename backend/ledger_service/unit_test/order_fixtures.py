"""A limit order row, for the tests that need a reservation to exist.

`test_reservations.py`, `test_overdraft_floor.py`, `test_trade.py` and
`test_ledger_routes.py` each need an order whose reservation is a known
figure, and none of them is testing how an order is placed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import Side
from model.entities import Order, OrderStatus


async def add_order(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    budget: str | None,
    filled_value: str = "0.0000",
    status: str = "open",
) -> Order:
    """One order of a single share at 0.5000, flushed, and returned.

    A buy when `budget` is given, a sell when it is None. A single share at
    0.5000 lets any budget from 0.5000 up pass
    `ck_orders_budget_covers_the_limit`, so a test chooses the reservation,
    `budget - filled_value`, directly. A filled order has its one share
    filled, a cancelled one a cancel time, and an open one half a share
    filled when anything has filled, so every CHECK on the table holds.
    """
    order_status = OrderStatus(status)
    if order_status is OrderStatus.FILLED:
        filled_quantity = Decimal("1.0000")
    elif Decimal(filled_value) > 0:
        filled_quantity = Decimal("0.5000")
    else:
        filled_quantity = Decimal("0.0000")

    order = Order(
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=Side.SELL if budget is None else Side.BUY,
        limit_price=Decimal("0.5000"),
        quantity=Decimal("1.0000"),
        filled_quantity=filled_quantity,
        budget=None if budget is None else Decimal(budget),
        filled_value=Decimal(filled_value),
        status=order_status,
        book_state_version=0,
        idempotency_key=f"order:{uuid.uuid4()}",
        cancelled_at=(
            datetime.now(UTC) if order_status is OrderStatus.CANCELLED else None
        ),
    )
    session.add(order)
    await session.flush()
    return order
