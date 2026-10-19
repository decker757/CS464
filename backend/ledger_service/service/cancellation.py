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

**An administrator can cancel any live order, with a reason** ([T-8] #326).
The same path, the same lock and the same refusals, without the owner check,
and with an `order.cancelled_by_admin` audit entry on the cancel's own
session, so the entry and the cancel commit together (ADR 0006). The owner's
own cancel appends nothing (D-NEW "An administrator can cancel any live
order, with a reason").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    CancellationIncomplete,
    OrderAlreadyCancelled,
    OrderAlreadyFilled,
    OrderNotFound,
    OrderReleased,
)
from model.audit import AdminAction
from model.entities import Order, OrderStatus
from service import audit, books
from service.audit import Actor

# The order object, built the way the placement and the order list build it.
from service.orders import OrderView, _order_view_of, _views_query

# Auth's suspension floor, which market_service's close and reject reasons use.
_MIN_REASON_LENGTH = 10


@dataclass(frozen=True)
class Cancellation:
    """The order as cancelled, and what the cancel released: credits for a
    buy, shares for a sell, the other None."""

    order: OrderView
    released_credits: Decimal | None
    released_shares: Decimal | None


@dataclass(frozen=True)
class AdminCancel:
    """An administrator cancelling an order that need not be theirs: who, and
    why. The reason is checked by `cancel`, untrimmed here."""

    actor: Actor
    reason: str


def _refuse_if_reason_unusable(reason: str) -> None:
    """Raise `CancellationIncomplete` unless the reason is at least ten
    characters once trimmed. A copy of auth's `user_admin._reason_problems`
    rule; different services, so the two may move apart. ADR 0012."""
    if len(reason.strip()) < _MIN_REASON_LENGTH:
        raise CancellationIncomplete


async def _get_order_market(
    session: AsyncSession, order_id: uuid.UUID, *, user_id: uuid.UUID | None, any_owner: bool
) -> uuid.UUID:
    """The market of the caller's order, or of anyone's when `any_owner`, read
    without a lock, only to know which book to lock. Raises `OrderNotFound` if
    there is no such order or it is not the caller's: an order's market and
    owner never change, so the answer cannot go stale. One column, so no
    `Order` is loaded that the read under the lock would then be answered
    from."""
    stmt = select(Order.market_id).where(Order.id == order_id)
    if not any_owner:
        stmt = stmt.where(Order.user_id == user_id)
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


async def _record_admin_cancel(session: AsyncSession, admin: AdminCancel, order: Order) -> None:
    """Append `order.cancelled_by_admin` on the cancel's session, at its
    `cancelled_at`, without committing: the cancel and its entry commit
    together. The order's owner and market go in `context`, the trimmed
    reason in `reason`."""
    await audit.record(
        session,
        actor=admin.actor,
        action=AdminAction.ORDER_CANCELLED_BY_ADMIN,
        target_type="order",
        target_id=order.id,
        reason=admin.reason.strip(),
        context={"user_id": str(order.user_id), "market_id": str(order.market_id)},
        now=order.cancelled_at,
    )


async def cancel(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None,
    admin: AdminCancel | None = None,
) -> Cancellation:
    """Cancel an open order and release its remainder. Commits.

    The owner's cancel passes their `user_id` and reaches only their orders.
    An administrator's passes `user_id=None` and `admin`, reaches any order,
    and appends `order.cancelled_by_admin` in the same transaction.

    Locks the order's book row, then reads the order under it; takes no
    account lock. Raises `CancellationIncomplete` (an administrator's only,
    before any read), `OrderNotFound`, `OrderAlreadyFilled`,
    `OrderAlreadyCancelled` or `OrderReleased`, each before writing anything.
    """
    if admin is not None:
        _refuse_if_reason_unusable(admin.reason)
    market_id = await _get_order_market(
        session, order_id, user_id=user_id, any_owner=admin is not None
    )
    await books.lock_book(session, market_id)
    order, position, live = await _read_order(session, order_id)
    _refuse_if_ended(order, live=live)

    # What the order holds just before the cancel is what the cancel releases.
    before = _order_view_of(order, outcome_position=position, live=True)
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = datetime.now(UTC)
    if admin is not None:
        await _record_admin_cancel(session, admin, order)
    await session.commit()

    return Cancellation(
        order=_order_view_of(order, outcome_position=position, live=False),
        released_credits=before.reserved_credits,
        released_shares=before.reserved_shares,
    )
