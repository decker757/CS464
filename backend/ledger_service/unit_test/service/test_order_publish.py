"""The order frames a fill puts on the bus. [T-7] #27, ADR 0024, ADR 0010.

#27's fifth criterion: "Filled and partially-filled orders push notifications
to the owner via websocket". What the ledger owes the relay, per
docs/api/realtime-service.md, "Order updates": one `order.update` message per
fill, after the commit that wrote it and after that commit's one price frame,
for a trade's cascade and a placement's own fill alike, and a failed publish
never fails the request. The producer itself is `test_price_publish.py`'s.

The figures are `test_cascade.py`'s and `test_place_order.py`'s, worked outside
the code there. A resting placement and a replay already assert that they
publish nothing at all, in `test_place_order.py` and `test_trade_replay.py`,
and a cancel is handed no Redis client.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Order, OrderStatus, Transaction
from unit_test.order_fixtures import add_order
from unit_test.placement_fixtures import limit_upstream, open_at, order_row, place
from unit_test.service.test_cascade import (
    BIG_TRADE,
    FIRST_TIED,
    FIRST_TIED_COST,
    LARGE,
    LARGE_FILL,
    LARGE_FILL_COST,
    LOWER_LIMIT,
    SECOND_TIED,
    SECOND_TIED_COST,
    TIED_LIMIT,
    ZERO_Q,
)
from unit_test.service.test_place_order import (
    EXAMPLE_COST,
    EXAMPLE_FILL,
    EXAMPLE_LIMIT,
    EXAMPLE_QUANTITY,
    _example_market,
    _funded,
)
from unit_test.trade_fixtures import (
    Recorder,
    assert_ledger_balances,
    buy,
    session_factory,
)

_PRICE = "market.price"
_ORDER = "order.update"


def _frames(recorder: Recorder) -> list[dict]:
    return [json.loads(payload) for payload in recorder.payloads_on(_ORDER)]


def _figures(frame: dict) -> tuple:
    """What one frame says happened: whose order, its state after the fill,
    and the fill."""
    fill = frame["fill"]
    return (
        uuid.UUID(frame["order_id"]),
        uuid.UUID(frame["user_id"]),
        frame["status"],
        Decimal(frame["quantity"]),
        Decimal(frame["filled_quantity"]),
        Decimal(fill["quantity"]),
        Decimal(fill["total"]),
        fill["state_version"],
    )


async def _fill_transaction_ids(session: AsyncSession, market_id: uuid.UUID) -> dict:
    """Each committed fill's transaction id, by its order id and version."""
    session.expire_all()
    stmt = select(Transaction).where(
        Transaction.context["market_id"].astext == str(market_id),
        Transaction.context.has_key("order_id"),
    )
    return {
        (uuid.UUID(t.context["order_id"]), t.context["state_version"]): t.id
        for t in (await session.execute(stmt)).scalars()
    }


async def _three_resting_orders(session: AsyncSession):
    """`test_cascade.py`'s three buys of outcome 1 on a flat book: one at
    0.4000 placed first, then two of one owner's at 0.4500."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    large_owner, tied_owner = await _funded(session), await _funded(session)
    large = await place(
        session, upstream, user_id=large_owner, outcome=1, limit_price=LOWER_LIMIT, quantity=LARGE
    )
    first = await place(
        session, upstream, user_id=tied_owner, outcome=1, limit_price=TIED_LIMIT, quantity=FIRST_TIED
    )
    second = await place(
        session, upstream, user_id=tied_owner, outcome=1, limit_price=TIED_LIMIT, quantity=SECOND_TIED
    )
    return upstream, (large_owner, large.order.id), (tied_owner, first.order.id, second.order.id)


# =========================================================================
# A trade's cascade
# =========================================================================
async def test_a_trade_that_fills_three_orders_sends_one_frame_per_fill(
    session: AsyncSession,
) -> None:
    """The big trade crosses three resting orders: the price frame at the
    book's final version 4, then one frame per fill in the order they filled,
    each to its order's owner, `filled` for the two that complete and `open`
    for the one that fills in part. Each names the fill's own transaction and
    version, which is what the client orders them by."""
    upstream, (large_owner, large), (tied_owner, first, second) = await _three_resting_orders(
        session
    )
    recorder = Recorder()

    trader = await _funded(session)
    await buy(session, upstream, user_id=trader, quantity=BIG_TRADE, redis_client=recorder)

    assert [channel for channel, _ in recorder.calls] == [_PRICE, _ORDER, _ORDER, _ORDER]
    frames = _frames(recorder)
    assert [_figures(frame) for frame in frames] == [
        (first, tied_owner, "filled", FIRST_TIED, FIRST_TIED, FIRST_TIED, -FIRST_TIED_COST, 2),
        (second, tied_owner, "filled", SECOND_TIED, SECOND_TIED, SECOND_TIED, -SECOND_TIED_COST, 3),
        (large, large_owner, "open", LARGE, LARGE_FILL, LARGE_FILL, -LARGE_FILL_COST, 4),
    ]
    committed = await _fill_transaction_ids(session, upstream.market_id)
    assert [uuid.UUID(frame["fill"]["transaction_id"]) for frame in frames] == [
        committed[(first, 2)],
        committed[(second, 3)],
        committed[(large, 4)],
    ]
    assert {(f["market_id"], f["outcome_id"], f["side"]) for f in frames} == {
        (str(upstream.market_id), str(upstream.outcomes[1]), "buy")
    }


# =========================================================================
# A placement's own fill, and its cascade
# =========================================================================
async def test_a_placement_that_fills_sends_its_owner_a_frame(session: AsyncSession) -> None:
    """The contract's example: a buy of 100 at 0.6000 fills 20.5465 for
    11.8152 on arrival and rests. The response tells this tab; the frame tells
    the owner's other tabs, after the price frame."""
    upstream = await _example_market(session)
    owner = await _funded(session)
    recorder = Recorder()

    placement = await place(
        session,
        upstream,
        user_id=owner,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
        redis_client=recorder,
    )

    assert [channel for channel, _ in recorder.calls] == [_PRICE, _ORDER]
    (frame,) = _frames(recorder)
    assert _figures(frame) == (
        placement.order.id,
        owner,
        "open",
        EXAMPLE_QUANTITY,
        EXAMPLE_FILL,
        EXAMPLE_FILL,
        -EXAMPLE_COST,
        1,
    )
    assert frame["fill"]["transaction_id"] == str(placement.fills[0].transaction_id)


async def test_a_placement_whose_cascade_fills_an_order_sends_both_owners_a_frame(
    session: AsyncSession,
) -> None:
    """`test_place_order.py`'s broken book: a buy of one outcome-1 share at
    0.5000 left resting while the price makes it executable. A buy of 10
    outcome-1 shares at 0.4700 fills 7.9855 for 3.6740 on arrival, and its
    cascade fills that share for 0.4713 at version 2: two fills, two frames,
    the placer's first."""
    upstream = await _example_market(session)
    owner, placer = await _funded(session), await _funded(session)
    broken = await add_order(
        session,
        user_id=owner,
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[1],
        budget="0.5000",
    )
    await session.commit()
    recorder = Recorder()

    placement = await place(
        session,
        upstream,
        user_id=placer,
        outcome=1,
        limit_price=Decimal("0.4700"),
        quantity=Decimal("10.0000"),
        redis_client=recorder,
    )

    one = Decimal("1.0000")
    assert [channel for channel, _ in recorder.calls] == [_PRICE, _ORDER, _ORDER]
    assert [_figures(frame) for frame in _frames(recorder)] == [
        (
            placement.order.id,
            placer,
            "open",
            Decimal("10.0000"),
            Decimal("7.9855"),
            Decimal("7.9855"),
            Decimal("-3.6740"),
            1,
        ),
        (broken.id, owner, "filled", one, one, one, Decimal("-0.4713"), 2),
    ]


# =========================================================================
# After the commit, and never failing it
# =========================================================================
async def test_an_order_frame_is_published_after_its_fill_has_committed(
    session: AsyncSession,
) -> None:
    """ADR 0010: after the commit, never before, or a rollback un-makes a fill
    the owner was told about. Checked from a second session while the order
    frame is in flight: under READ COMMITTED it sees the placed order, filled,
    only if the commit already happened."""
    upstream = await _example_market(session)
    owner = await _funded(session)
    recorder = Recorder()
    seen: list[Decimal | None] = []

    async def look_from_another_session() -> None:
        channel, payload = recorder.calls[-1]
        if channel != _ORDER:
            return
        order_id = uuid.UUID(json.loads(payload)["order_id"])
        async with session_factory()() as other:
            row = (
                await other.execute(select(Order.filled_quantity).where(Order.id == order_id))
            ).scalar_one_or_none()
        seen.append(row)

    recorder.on_publish = look_from_another_session

    await place(
        session,
        upstream,
        user_id=owner,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
        redis_client=recorder,
    )

    assert seen == [EXAMPLE_FILL], (
        "the order frame was published before the fill it announces committed"
    )


async def test_a_failed_order_publish_leaves_the_trade_written(session: AsyncSession) -> None:
    """The publish must never fail the trade (ADR 0010): every order frame
    throws, and the trade still answers, every fill is still committed, and
    each frame was still attempted, since one failure stops none of the
    others."""
    upstream, (_, large), (_, first, second) = await _three_resting_orders(session)
    recorder = Recorder()

    async def fail_on_the_order_channel() -> None:
        if recorder.calls[-1][0] == _ORDER:
            raise ConnectionError("redis is unreachable")

    recorder.on_publish = fail_on_the_order_channel

    trader = await _funded(session)
    trade = await buy(
        session, upstream, user_id=trader, quantity=BIG_TRADE, redis_client=recorder
    )

    assert trade.book_state_version == 4
    assert len(recorder.payloads_on(_ORDER)) == 3
    async with session_factory()() as other:
        assert (await order_row(other, first)).status is OrderStatus.FILLED
        assert (await order_row(other, second)).status is OrderStatus.FILLED
        assert (await order_row(other, large)).filled_quantity == LARGE_FILL
        await assert_ledger_balances(other)
