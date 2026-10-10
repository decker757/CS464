"""The trade path's latch: a settled market refuses a trade under the book
lock. [3.4] #12, ADR 0019, DECISIONS.md "The trade latch sits after the key
re-check under the book lock, and before the outcomes read and the staleness
check".

The gate is stubbed open in each test, so the status gate, which already
refuses a market market_service reports as settled, cannot be what refuses.
The race against a settlement is in `test_settlement_concurrency.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.settlement_fixtures import (
    LOSER,
    WINNER,
    SettleUpstream,
    errors,
    hold,
    session_factory,
    settle,
    warm,
)
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    book_row,
    buy,
    entry_count,
    fund,
    pool_balance,
    trading,
    transaction_count,
)


@pytest.mark.parametrize("quote", ["current", "stale"])
async def test_a_settled_market_refuses_a_trade_its_gate_let_through(
    session: AsyncSession, quote: str
) -> None:
    """#12: a trade on a settled market is `409 market_closed`, and writes
    nothing.

    With the current `state_version` quoted, nothing but the latch can refuse:
    the gate is open, the trader is funded and the outcome is known. With a
    stale one, the answer is still `market_closed`, not `quote_stale`: a fresh
    quote cannot help on a market that will never trade again. Move the latch
    after the staleness check and the stale case answers `quote_stale`.
    """
    upstream = SettleUpstream()
    await warm(session, upstream)
    await hold(
        session,
        upstream,
        [(uuid.uuid4(), WINNER, Decimal("41.3337")), (uuid.uuid4(), LOSER, Decimal("17.0429"))],
    )
    await settle(session, upstream)
    trader = uuid.uuid4()
    await fund(session, trader)
    version = (await book_row(session, upstream.market_id)).state_version
    entries = await entry_count(session)

    with pytest.raises(errors().MarketClosed):
        await buy(
            session,
            upstream,
            user_id=trader,
            outcome=WINNER,
            quantity=SMALL_QUANTITY,
            state_version=version if quote == "current" else version + 7,
            client_key="after-settlement",
            transport=upstream.gate_open,
        )

    key = trading().trade_key(trader, upstream.market_id, "after-settlement")
    async with session_factory()() as other:
        assert await transaction_count(other, key=key) == 0
        assert await entry_count(other) == entries
        assert await pool_balance(other, upstream.market_id) == Decimal("0.0000")
