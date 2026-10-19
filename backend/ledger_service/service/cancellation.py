"""Cancelling a limit order. [T-8] #28, ADR 0024.

A cancel sets the order's status and `cancelled_at`, and nothing else. It
writes no entry and moves no money: what an order holds back is derived from
its row (ADR 0024, "A reservation is derived from the order rows"), so once
the row reads `cancelled` the credits or shares are free, and a second cancel
has nothing left to release.

**A cancel and a fill never both win.** Every writer of an order row takes the
market's book row lock first, and a fill only happens inside a trade or a
placement holding it. So the cancel takes the same lock, and reads the order
again under it before deciding anything: whichever request took the lock first
committed first, and the cancel sees what it left (D-NEW "Cancel calls neither
auth nor the market gate", D-NEW "A cancel takes the book lock and no account
lock").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    OrderAlreadyCancelled,
    OrderAlreadyFilled,
    OrderNotFound,
    OrderReleased,
)
from model.entities import Order, OrderStatus
from service import books

# The order object, built the way the placement and the order list build it.
from service.orders import OrderView, _order_view_of, _views_query


@dataclass(frozen=True)
class Cancellation:
    """The order as cancelled, and what the cancel released: credits for a
    buy, shares for a sell, the other None."""

    order: OrderView
    released_credits: Decimal | None
    released_shares: Decimal | None


async def _get_order_market(
    session: AsyncSession, order_id: uuid.UUID, *, user_id: uuid.UUID
) -> uuid.UUID:
    """The market of the caller's order, read without a lock, only to know
    which book to lock. Raises `OrderNotFound` if there is no such order or it
    is not the caller's: an order's market and owner never change, so the
    answer cannot go stale. One column, so no `Order` is loaded that the read
    under the lock would then be answered from."""
    stmt = select(Order.market_id).where(Order.id == order_id, Order.user_id == user_id)
    market_id = (await session.execute(stmt)).scalar_one_or_none()
    if market_id is None:
        raise OrderNotFound
    return market_id


async def _read_order(session: AsyncSession, order_id: uuid.UUID) -> Row[tuple[Order, int, bool]]:
    """The order with its outcome's position and whether it is live. Call it
    holding the book lock, which holds the row still: no lock of its own."""
    return (await session.execute(_views_query().where(Order.id == order_id))).one()


def _refuse_if_ended(order: Order, *, live: bool) -> None:
    """Raise unless the order is open with no result on its market. The status
    first: a filled or cancelled order is never live, so `live` first would
    call every one of them released."""
    if order.status is OrderStatus.FILLED:
        raise OrderAlreadyFilled
    if order.status is OrderStatus.CANCELLED:
        raise OrderAlreadyCancelled
    if not live:
        raise OrderReleased


async def cancel(
    session: AsyncSession, order_id: uuid.UUID, *, user_id: uuid.UUID
) -> Cancellation:
    """Cancel the caller's open order and release its remainder. Commits.

    Locks the order's book row, then reads the order under it; takes no
    account lock. Raises `OrderNotFound`, `OrderAlreadyFilled`,
    `OrderAlreadyCancelled` or `OrderReleased`, each before writing anything.
    """
    market_id = await _get_order_market(session, order_id, user_id=user_id)
    await books.lock_book(session, market_id)
    order, position, live = await _read_order(session, order_id)
    _refuse_if_ended(order, live=live)

    # What the order holds just before the cancel is what the cancel releases.
    before = _order_view_of(order, outcome_position=position, live=True)
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = datetime.now(UTC)
    await session.commit()

    return Cancellation(
        order=_order_view_of(order, outcome_position=position, live=False),
        released_credits=before.reserved_credits,
        released_shares=before.reserved_shares,
    )
