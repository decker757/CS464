"""A market's price history. [BE][2.4] #63

Rebuilt from the market's trades on every read, with nothing stored:
DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it". The trades are read from the market's own pool feed,
oldest first and with no time condition, because the window is cut after the
replay (DECISIONS.md, "The `from`/`to` window and the carry-in point are
applied in `core`, after the replay of every trade"). The result sits beside
the points, which end at the last trade ("Settlement adds no point to the
price history; the series ends at the last trade").

Like the exposure read, it writes nothing, opens no book, mints no grant and
never calls market_service or auth ("Price history takes any valid token; the
trade feed takes an administrator's", Notes).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import PriceHistoryInconsistent
from core.price_history import ReplayedTrade, price_history_of, window_of
from model.entities import Entry, ResultKind, Transaction
from service import book_prices, books, ledger_service, trading
from service.book_prices import PricedOutcome


@dataclass(frozen=True)
class PricedPoint:
    """The prices the book held at one `state_version`, ready for the wire."""

    state_version: int
    occurred_at: datetime
    prices: list[PricedOutcome]


@dataclass(frozen=True)
class PriceHistory:
    """A market's points, and how it ended if it has. A market this service
    holds no book for has no points and no result."""

    market_id: uuid.UUID
    points: list[PricedPoint]
    result: ResultKind | None
    winning_outcome_id: uuid.UUID | None
    result_recorded_at: datetime | None


async def _replayed_trades_of(
    session: AsyncSession, pool_account_id: uuid.UUID, outcomes: Sequence[Row]
) -> list[ReplayedTrade]:
    """Every trade on the pool's feed, oldest first.

    Ordered like `ix_ledger_entries_account_feed`, so the order is the order
    the trades committed in, which is `state_version` order. Each row is read
    by `trade_fields_of` and `trading.result_of`, the readers the trade feed
    uses, so a trade means the same thing on both routes.
    """
    position_of = {row.outcome_id: row.position for row in outcomes}
    query = ledger_service.pool_trade_query(pool_account_id).order_by(
        Entry.created_at, Entry.id
    )

    trades = []
    for row in (await session.execute(query)).all():
        transaction = Transaction(id=row.transaction_id, kind=row.kind, context=row.context)
        fields = ledger_service.trade_fields_of(transaction, row.amount)
        trades.append(
            ReplayedTrade(
                position=position_of[fields.outcome_id],
                side=fields.side,
                quantity=fields.quantity,
                state_version=trading.result_of(transaction).state_version,
                occurred_at=row.created_at,
            )
        )
    return trades


async def price_history_of_market(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    start: datetime | None,
    end: datetime | None,
) -> PriceHistory:
    """Every price this market has had inside `start` and `end`, plus the
    carry-in point when `start` is given. None is no bound. Writes nothing.

    Raises `MarketBookIncomplete` for a book the engine cannot price, and
    `PriceHistoryInconsistent` for trades whose `state_version`s do not run
    1, 2, 3, ... in order. A market with no book is an empty history.
    """
    book = await books.find(session, market_id)
    if book is None:
        return PriceHistory(
            market_id=market_id,
            points=[],
            result=None,
            winning_outcome_id=None,
            result_recorded_at=None,
        )

    outcomes = await book_prices._read(session, market_id)
    book_prices.refuse_unpriceable(len(outcomes), book.liquidity_b)

    trades = await _replayed_trades_of(session, book.pool_account_id, outcomes)
    try:
        replayed = price_history_of(
            trades=trades,
            outcome_count=len(outcomes),
            b=book.liquidity_b,
            opened_at=book.opened_at,
        )
    except ValueError as error:
        raise PriceHistoryInconsistent from error

    points = [
        PricedPoint(
            state_version=point.state_version,
            occurred_at=point.occurred_at,
            prices=book_prices.priced(outcomes, list(point.prices)),
        )
        for point in window_of(replayed, start=start, end=end)
    ]

    result = await books.find_result(session, market_id)
    return PriceHistory(
        market_id=market_id,
        points=points,
        result=None if result is None else result.kind,
        winning_outcome_id=None if result is None else result.outcome_id,
        result_recorded_at=None if result is None else result.recorded_at,
    )
