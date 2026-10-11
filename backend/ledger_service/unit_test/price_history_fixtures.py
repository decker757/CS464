"""Fixtures the [BE][2.4] #63 price-history test files share.

Built on `portfolio_fixtures.py`, `sell_fixtures.py` and `trade_feed_fixtures.py`
rather than beside them. Every trade goes through `trading.execute`, the path
the trade route calls, and no `q` is ever set by hand.

- `traded`: one market at b = 100, opened at zero. Trader A buys 50 of
  outcome 0, trader B buys 20 of outcome 1, A sells 20 of outcome 0. Its
  outcome ids are in descending uuid order, so a read ordered by
  `outcome_id` rather than `position` puts them the wrong way round.
- `gapped`: one real trade, then a balanced `trade_buy` movement posted
  through `posting.post` whose context claims `state_version` 3. Money moves,
  nothing is set behind the ledger's back, and the pool's feed runs 1, 3.

The expected times come from `ledger.entries` and the book row, read here,
never from the read under test.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.history_fixtures import stored_context
from unit_test.sell_fixtures import hold, open_at_zero, sell
from unit_test.trade_fixtures import Upstream, accounts_module, entities, grants, posting

B = Decimal("100.0000")

# Worked by hand with float `exp`, not with `core/lmsr.py`: for two outcomes
# p0 = 1 / (1 + e^-((q0 - q1) / b)).
#   sv1, q = [50, 0]:  1 / (1 + e^-0.5) = 0.622459
#   sv2, q = [50, 20]: 1 / (1 + e^-0.3) = 0.574443
#   sv3, q = [30, 20]: 1 / (1 + e^-0.1) = 0.524979
# The wire's strings are those at scale 4, ROUND_HALF_UP.
RAW_PRICES = {
    1: (Decimal("0.622459"), Decimal("0.377541")),
    2: (Decimal("0.574443"), Decimal("0.425557")),
    3: (Decimal("0.524979"), Decimal("0.475021")),
}
WIRE_PRICES = {
    0: ("0.5000", "0.5000"),
    1: ("0.6225", "0.3775"),
    2: ("0.5744", "0.4256"),
    3: ("0.5250", "0.4750"),
}


@dataclass(frozen=True)
class Traded:
    """`traded`'s market and its three trades, oldest first."""

    upstream: Upstream
    trader_a: uuid.UUID
    trader_b: uuid.UUID
    a_buy: object
    b_buy: object
    a_sell: object

    @property
    def market_id(self) -> uuid.UUID:
        return self.upstream.market_id

    @property
    def trades(self) -> list:
        return [self.a_buy, self.b_buy, self.a_sell]


def _descending_market() -> Upstream:
    """A market at b = 100 whose outcome ids run high to low by position.

    The body is rebuilt because `Upstream` builds it from the ids once, in
    its constructor, and the book's outcome rows come from the body.
    """
    upstream = Upstream()
    upstream.outcomes = sorted(upstream.outcomes, reverse=True)
    upstream.body["outcomes"] = [
        {"id": str(outcome_id), "position": position, "label": f"Outcome {position}"}
        for position, outcome_id in enumerate(upstream.outcomes)
    ]
    upstream.body["liquidity_b"] = str(B)
    return upstream


async def traded(session: AsyncSession) -> Traded:
    upstream = _descending_market()
    await open_at_zero(session, upstream)
    trader_a = uuid.uuid4()
    trader_b = uuid.uuid4()
    a_buy = await hold(session, upstream, user_id=trader_a, quantity=Decimal("50"), outcome=0)
    b_buy = await hold(session, upstream, user_id=trader_b, quantity=Decimal("20"), outcome=1)
    a_sell = await sell(session, upstream, user_id=trader_a, quantity=Decimal("20"), outcome=0)

    assert [trade.state_version for trade in (a_buy, b_buy, a_sell)] == [1, 2, 3]
    assert upstream.outcomes[0] > upstream.outcomes[1]
    return Traded(
        upstream=upstream,
        trader_a=trader_a,
        trader_b=trader_b,
        a_buy=a_buy,
        b_buy=b_buy,
        a_sell=a_sell,
    )


async def gapped(session: AsyncSession) -> Upstream:
    """A market whose pool feed holds trades labelled 1, then 3.

    The second is a real, balanced movement through `posting.post`, kind
    `trade_buy`, carrying trade 1's context with `state_version` 3. The book's
    own `state_version` stays at 1. Committed.
    """
    upstream = _descending_market()
    await open_at_zero(session, upstream)
    user_id = uuid.uuid4()
    first = await hold(session, upstream, user_id=user_id, quantity=Decimal("50"), outcome=0)

    context = dict(await stored_context(session, first.transaction_id))
    context["state_version"] = 3
    ents = entities()
    user_account = await grants().ensure_granted(session, user_id)
    pool_account = await accounts_module().find(
        session, ents.AccountKind.MARKET_POOL, upstream.market_id
    )
    amount = first.total.copy_abs()
    await posting().post(
        session,
        idempotency_key=f"price-history-gap:{uuid.uuid4()}",
        kind=ents.TransactionKind.TRADE_BUY,
        legs=[
            posting().Leg(account=user_account, amount=-amount),
            posting().Leg(account=pool_account, amount=amount),
        ],
        context=context,
    )
    return upstream


async def entry_times(
    session: AsyncSession, transaction_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """When each transaction's entries landed, from `ledger.entries`. Both legs
    of a movement share one stamp; this asserts they do."""
    entry = entities().Entry
    rows = (
        await session.execute(
            select(entry.transaction_id, entry.created_at).where(
                entry.transaction_id.in_(transaction_ids)
            )
        )
    ).all()
    times: dict[uuid.UUID, datetime] = {}
    for transaction_id, created_at in rows:
        assert times.setdefault(transaction_id, created_at) == created_at
    assert set(times) == set(transaction_ids)
    return times


async def opened_at_of(session: AsyncSession, market_id: uuid.UUID) -> datetime:
    book = entities().MarketBook
    return (
        await session.execute(select(book.opened_at).where(book.market_id == market_id))
    ).scalar_one()


async def result_row_of(session: AsyncSession, market_id: uuid.UUID):
    """`(outcome_id, recorded_at)` from `ledger.market_results`, as stored."""
    result = entities().MarketResult
    return (
        await session.execute(
            select(result.outcome_id, result.recorded_at).where(
                result.market_id == market_id
            )
        )
    ).one()
