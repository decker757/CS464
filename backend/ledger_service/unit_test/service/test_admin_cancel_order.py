"""An administrator cancels any trader's live order, with a reason. [T-8] #326,
ADR 0024.

Business rules, driven through `service/cancellation.py::cancel` with an
`AdminCancel` and no HTTP. Who may call it, and the bounds the body enforces,
are `unit_test/controller/test_admin_cancel_routes.py`'s; an administrator's
cancel racing the owner's is `test_cancel_order_concurrency.py`'s.

#326: "An administrator's token can cancel any order, not just their own",
"A reason is required, and the cancel writes an audit entry on the ledger's
own seam, in the cancel's transaction (ADR 0006). The owner's own cancel
stays unaudited", and "Nothing is posted".

The figures are the placement suite's: a resting buy of 13.3333 at 0.4321
holds 5.7614.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    CancellationIncomplete,
    OrderAlreadyCancelled,
    OrderAlreadyFilled,
    OrderNotFound,
    OrderReleased,
)
from model.entities import OrderStatus
from service import cancellation
from unit_test.placement_fixtures import place
from unit_test.service.test_cancel_order import _committed, _ended
from unit_test.service.test_place_order import (
    RESTING_BUDGET,
    RESTING_LIMIT,
    RESTING_QUANTITY,
    _example_market,
    _funded,
)
from unit_test.settlement_fixtures import admin, entries_naming
from unit_test.trade_fixtures import transaction_count

NOTHING = Decimal("0.0000")
REASON = "The owner is suspended; releasing what the order holds."


def _by_admin(reason: str = REASON, actor=None) -> cancellation.AdminCancel:  # noqa: ANN001
    return cancellation.AdminCancel(actor=actor or admin(), reason=reason)


async def _resting(session: AsyncSession):
    """The market, a funded owner and their resting buy's id. Committed."""
    upstream = await _example_market(session)
    owner = await _funded(session)
    placed = await place(
        session, upstream, user_id=owner, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )
    return upstream, owner, placed.order.id


# =========================================================================
# What it cancels
# =========================================================================
async def test_an_administrator_cancels_another_traders_live_order_and_releases_its_budget(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """#326: "An administrator's token can cancel any order, not just their
    own", and "Nothing is posted ... setting the status releases it, as the
    owner's cancel does". The administrator owns nothing here, so no owner
    check can be what let it through."""
    _, owner, order_id = await _resting(session)
    transactions = await transaction_count(session)

    cancelled = await cancellation.cancel(session, order_id, user_id=None, admin=_by_admin())

    assert (cancelled.released_credits, cancelled.released_shares) == (RESTING_BUDGET, None)
    assert cancelled.order.status == "cancelled"
    row, balance = await _committed(order_id, owner)
    assert row.status is OrderStatus.CANCELLED
    assert row.cancelled_at is not None
    assert (balance.amount, balance.reserved, balance.available) == (
        starting_credits,
        NOTHING,
        starting_credits,
    )
    assert await transaction_count(session) == transactions


@pytest.mark.parametrize(
    ("ending", "refusal"),
    [
        ("filled", OrderAlreadyFilled),
        ("cancelled", OrderAlreadyCancelled),
        ("released", OrderReleased),
    ],
    ids=["filled", "cancelled", "released"],
)
async def test_an_order_that_has_ended_is_refused_as_the_owners_cancel_refuses_it(
    session: AsyncSession, ending: str, refusal: type[Exception]
) -> None:
    """Exactly the orders the owner could cancel: an order that is not live
    gets the owner's own refusal, and is left as it was."""
    order_id, owner = await _ended(session, ending)
    before, _ = await _committed(order_id, owner)

    with pytest.raises(refusal):
        await cancellation.cancel(session, order_id, user_id=None, admin=_by_admin())

    after, _ = await _committed(order_id, owner)
    assert (after.status, after.cancelled_at, after.filled_quantity) == (
        before.status,
        before.cancelled_at,
        before.filled_quantity,
    )


async def test_an_id_that_names_no_order_is_order_not_found(session: AsyncSession) -> None:
    with pytest.raises(OrderNotFound):
        await cancellation.cancel(session, uuid.uuid4(), user_id=None, admin=_by_admin())


# =========================================================================
# The reason
# =========================================================================
@pytest.mark.parametrize(
    "reason", ["   ", " 123456789 "], ids=["blank", "nine_once_trimmed"]
)
async def test_a_reason_under_ten_characters_once_trimmed_is_refused_and_the_order_stays_open(
    session: AsyncSession, reason: str
) -> None:
    """The suspension's rule: trimmed, then at least ten characters. Nine
    characters padded to eleven is refused, so the floor counts what is left
    after the trim."""
    _, owner, order_id = await _resting(session)

    with pytest.raises(CancellationIncomplete):
        await cancellation.cancel(session, order_id, user_id=None, admin=_by_admin(reason))

    row, balance = await _committed(order_id, owner)
    assert (row.status, row.cancelled_at) == (OrderStatus.OPEN, None)
    assert balance.reserved == RESTING_BUDGET


async def test_a_reason_of_exactly_ten_characters_once_trimmed_is_accepted_and_logged_trimmed(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The floor's other edge: ten characters is enough. The log keeps the
    trimmed reason, as the suspension's does."""
    _, _, order_id = await _resting(session)

    await cancellation.cancel(session, order_id, user_id=None, admin=_by_admin(" 1234567890 "))

    [entry] = await entries_naming(audit_reader, order_id)
    assert entry["reason"] == "1234567890"


# =========================================================================
# The audit entry
# =========================================================================
async def test_the_cancel_appends_order_cancelled_by_admin_naming_the_actor_the_order_and_its_owner(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """#326: "the cancel writes an audit entry on the ledger's own seam, in
    the cancel's transaction (ADR 0006)". One entry, at the cancel's own
    `cancelled_at`, naming who cancelled, which order, whose and in which
    market, and why."""
    upstream, owner, order_id = await _resting(session)
    actor = admin("michelle_admin")

    await cancellation.cancel(session, order_id, user_id=None, admin=_by_admin(actor=actor))

    row, _ = await _committed(order_id, owner)
    [entry] = await entries_naming(audit_reader, order_id)
    assert entry["action_type"] == "order.cancelled_by_admin"
    assert (entry["actor_id"], entry["actor_username"], entry["actor_role"]) == (
        actor.id,
        "michelle_admin",
        "admin",
    )
    assert (entry["target_type"], entry["target_id"]) == ("order", order_id)
    assert entry["reason"] == REASON
    assert entry["context"] == {"user_id": str(owner), "market_id": str(upstream.market_id)}
    assert entry["source_service"] == "ledger_service"
    assert entry["occurred_at"] == row.cancelled_at


async def test_the_owners_own_cancel_appends_no_audit_entry(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """#326: "The owner's own cancel stays unaudited." Draft autosave's rule
    one level down: the log records administrators' decisions, and a trader
    withdrawing their own order is not one."""
    _, owner, order_id = await _resting(session)

    await cancellation.cancel(session, order_id, user_id=owner)

    assert await entries_naming(audit_reader, order_id) == []
