"""How much a market has traded, as the batch snapshot reports it. [X-1] #447.

`credits_traded` is every trade's credits summed, a buy's cost and a sell's
proceeds alike, a resting order's fills included (DECISIONS.md, "A market's
activity is the credits it has traded, buys and sells alike"). Read back
through `service/snapshot.py::list_snapshots`, its one reader. Two trades
racing over one book are `test_trade_concurrency.py`'s, and each market's own
figure is `test_snapshot_batch.py`'s.

The figures are literals worked outside the code: the sell suite's worked
example at `b = 137`, and at `b = 100` the placement suite's fill on arrival
and `test_cascade.py`'s trade and cascade fill after it, each pinned where it
is defined.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from service import snapshot
from unit_test.placement_fixtures import limit_upstream, open_at, place
from unit_test.sell_fixtures import HELD, SOLD, hold, open_at_zero, sell
from unit_test.trade_fixtures import Upstream, buy, fund, warm


async def _credits_traded(session: AsyncSession, market_id: uuid.UUID) -> Decimal:
    (listed,) = await snapshot.list_snapshots(
        session, [market_id], now=datetime.now(UTC)
    )
    return listed.credits_traded


async def test_an_untraded_book_is_answered_with_zero(session: AsyncSession) -> None:
    """#447: a market with no trades reports zero rather than being absent."""
    upstream = Upstream()
    await warm(session, upstream)

    listed = await snapshot.list_snapshots(
        session, [upstream.market_id], now=datetime.now(UTC)
    )

    assert [(item.market_id, item.credits_traded) for item in listed] == [
        (upstream.market_id, Decimal("0.0000"))
    ]


async def test_a_buy_adds_its_cost_and_a_sell_adds_its_proceeds(
    session: AsyncSession,
) -> None:
    """#447's DoD, the figure moves by the right amount on a buy and on a sell.
    Buying `54.3333` costs `29.8428` and selling `10` of it pays `5.8905`, so
    the figure is their sum; a signed sum would read `23.9523` or its
    negative."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)

    await hold(session, upstream, user_id=user_id, quantity=HELD)
    assert await _credits_traded(session, upstream.market_id) == Decimal("29.8428")

    await sell(session, upstream, user_id=user_id, quantity=SOLD)
    assert await _credits_traded(session, upstream.market_id) == Decimal("35.7333")


async def test_a_resting_orders_fills_count_on_arrival_and_in_a_cascade(
    session: AsyncSession,
) -> None:
    """A fill is a trade (ADR 0024), on either of its paths. The contract's
    example order, buy 100 outcome-0 shares at 0.6000, fills 20.5465 for
    11.8152 on arrival. Another trader's buy of 85 outcome-1 shares costs
    42.9090, and its cascade fills the remaining 79.4535 for 38.8002."""
    upstream = limit_upstream()
    await open_at(session, upstream)
    owner, trader = uuid.uuid4(), uuid.uuid4()
    await fund(session, owner)
    await fund(session, trader)

    await place(
        session,
        upstream,
        user_id=owner,
        limit_price=Decimal("0.6000"),
        quantity=Decimal("100.0000"),
    )
    assert await _credits_traded(session, upstream.market_id) == Decimal("11.8152")

    await buy(
        session,
        upstream,
        user_id=trader,
        outcome=1,
        quantity=Decimal("85.0000"),
        state_version=1,
    )
    assert await _credits_traded(session, upstream.market_id) == Decimal("93.5244")
