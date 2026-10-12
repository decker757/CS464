"""Fixtures the [BE][2.4] #63 trade-feed test files share.

Built on `history_fixtures.py`, `sell_fixtures.py` and `settlement_fixtures.py`
rather than beside them: the same `market_at`, `funded`, `hold`, `sell`,
`buy` and `settle`. What is added here is two scenarios, both reached through
the real paths, so the market's pool holds every kind of leg the feed has to
leave out:

- `feed`: two markets opened by `books.ensure_open`, each with its real
  `market_seed`. In M1, trader A makes #23's worked example (a buy of
  54.3333, then a sell sent as `"10"`), and only then trader B buys outcome
  1, so A's two trades keep the pinned averages. M2 has one buy by B.
- `settled_feed`: a settleable market, seeded, with one real buy on each
  outcome, then settled through the real `pay_out`. The pool then also holds
  a payout and the residue.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.history_fixtures import SENT_AS_TEN
from unit_test.portfolio_fixtures import funded, market_at
from unit_test.sell_fixtures import hold, sell, version_of
from unit_test.settlement_fixtures import (
    LOSER,
    WINNER,
    SettleUpstream,
    payout_key,
    residue_key,
    settle,
    transaction_by_key,
)
from unit_test.settlement_fixtures import warm as warm_settleable
from unit_test.trade_fixtures import SMALL_QUANTITY, Upstream, books, buy, fund


@dataclass(frozen=True)
class Feed:
    """Two markets. M1: A buys, A sells, B buys outcome 1. M2: B buys."""

    m1: Upstream
    m2: Upstream
    trader_a: uuid.UUID
    trader_b: uuid.UUID
    a_buy: object
    a_sell: object
    b_buy: object
    m2_buy: object

    @property
    def m1_newest_first(self) -> list:
        return [self.b_buy, self.a_sell, self.a_buy]


async def feed(session: AsyncSession) -> Feed:
    m1 = await market_at(session)
    trader_a, _ = await funded(session)
    trader_b, _ = await funded(session)
    a_buy = await hold(session, m1, user_id=trader_a)
    a_sell = await sell(session, m1, user_id=trader_a, quantity=SENT_AS_TEN)
    b_buy = await hold(session, m1, user_id=trader_b, quantity=SMALL_QUANTITY, outcome=1)

    m2 = await market_at(session)
    m2_buy = await hold(session, m2, user_id=trader_b, quantity=SMALL_QUANTITY)
    return Feed(
        m1=m1,
        m2=m2,
        trader_a=trader_a,
        trader_b=trader_b,
        a_buy=a_buy,
        a_sell=a_sell,
        b_buy=b_buy,
        m2_buy=m2_buy,
    )


@dataclass(frozen=True)
class SettledFeed:
    """A settled market: the winner's buy, then the loser's, newest last.
    `left_out` is the seed, the payout and the residue, by transaction id."""

    upstream: SettleUpstream
    winner_buy: object
    loser_buy: object
    left_out: list[uuid.UUID]


async def _buy_on(session: AsyncSession, upstream: SettleUpstream, outcome: int):
    """A real buy on a settleable market, its trade gate stubbed open."""
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=outcome,
        quantity=SMALL_QUANTITY,
        state_version=await version_of(session, upstream.market_id),
        client_key=f"feed-{uuid.uuid4()}",
        transport=upstream.gate_open,
    )


async def settled_feed(session: AsyncSession) -> SettledFeed:
    upstream = SettleUpstream()
    await warm_settleable(session, upstream)
    winner_buy = await _buy_on(session, upstream, WINNER)
    loser_buy = await _buy_on(session, upstream, LOSER)
    await settle(session, upstream)

    left_out = []
    for key in (
        books().market_open_key(upstream.market_id),
        payout_key(upstream.market_id, winner_buy.user_id),
        residue_key(upstream.market_id),
    ):
        transaction = await transaction_by_key(session, key)
        assert transaction is not None, f"the scenario wrote no {key}"
        left_out.append(transaction.id)
    return SettledFeed(
        upstream=upstream,
        winner_buy=winner_buy,
        loser_buy=loser_buy,
        left_out=left_out,
    )
