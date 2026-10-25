"""A market's price history. [BE][2.4] #63

Rebuilt from the market's trades on every read, with nothing stored:
DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it". The trades are read from the market's own pool feed,
oldest first and with no time condition, because the window is cut after the
replay (DECISIONS.md, "The `from`/`to` window and the carry-in point are
applied in `core`, after the replay of every trade"). The result sits beside
the points, which end at the last trade ("Settlement adds no point to the
price history; the series ends at the last trade").

`list_prices_at` is the same replay cut at one moment, for many markets at
once: the batch snapshot's prices a week ago ([X-1] #446).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Row, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import PriceHistoryInconsistent
from core.price_history import ReplayedTrade, price_history_of, window_of
from model.entities import Entry, MarketBook, ResultKind, Transaction
from service import book_prices, books, ledger_service, trading
from service.book_prices import PricedOutcome

logger = logging.getLogger(__name__)


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


def _replayed_trade_of(row: Row, position_of: Mapping[uuid.UUID, int]) -> ReplayedTrade:
    """One trade entry as the replay needs it. Read by `trade_fields_of` and
    `trading.result_of`, the readers the trade feed uses, so a trade means the
    same thing on both routes."""
    transaction = Transaction(id=row.transaction_id, kind=row.kind, context=row.context)
    fields = ledger_service.trade_fields_of(transaction, row.amount)
    return ReplayedTrade(
        position=position_of[fields.outcome_id],
        side=fields.side,
        quantity=fields.quantity,
        state_version=trading.result_of(transaction).state_version,
        occurred_at=row.created_at,
    )


async def _replayed_trades_of(
    session: AsyncSession, pool_account_id: uuid.UUID, outcomes: Sequence[Row]
) -> list[ReplayedTrade]:
    """Every trade on the pool's feed, oldest first, in `state_version`
    order (`ledger_service.pool_trades_oldest_first`)."""
    position_of = {row.outcome_id: row.position for row in outcomes}
    query = ledger_service.pool_trades_oldest_first(pool_account_id)
    rows = (await session.execute(query)).all()
    return [_replayed_trade_of(row, position_of) for row in rows]


def _trades_up_to_query(market_ids: Sequence[uuid.UUID], at: datetime) -> Select:
    """Every trade at or before `at` on these markets' pools, each row also
    carrying its `market_id` and the book's `opened_at`, ordered by market and
    then like the feed. `ix_ledger_entries_account_feed` serves each pool."""
    return (
        ledger_service.trade_entries_query()
        .add_columns(MarketBook.market_id, MarketBook.opened_at)
        .join(MarketBook, MarketBook.pool_account_id == Entry.account_id)
        .where(MarketBook.market_id.in_(market_ids), Entry.created_at <= at)
        .order_by(MarketBook.market_id, Entry.created_at, Entry.id)
    )


async def price_history_of_market(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    start: datetime | None,
    end: datetime | None,
) -> PriceHistory:
    """Every price this market has had inside `start` and `end`, plus the
    carry-in point when `start` is given. None is no bound.

    Writes nothing and never calls market_service or auth ("Price history takes
    any valid token; the trade feed takes an administrator's", Notes).

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

    outcomes = await book_prices.list_book_outcomes(session, market_id)
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


async def list_prices_at(
    session: AsyncSession,
    outcomes_by_market: Mapping[uuid.UUID, Sequence[Row]],
    *,
    at: datetime,
) -> dict[uuid.UUID, list[PricedOutcome]]:
    """Each market's prices as they stood at `at`, after its last trade at or
    before then. [X-1] #446.

    `outcomes_by_market` holds each market's book rows in position order, as
    `book_prices.list_book_outcomes_for_markets` reads them. One statement
    reads every market's trades, with no lock. A market with no trade by `at`
    is absent, and so is one whose trades cannot be replayed, which is logged.
    """
    query = _trades_up_to_query(list(outcomes_by_market), at)
    trades_by_market: dict[uuid.UUID, list[Row]] = {}
    for row in (await session.execute(query)).all():
        trades_by_market.setdefault(row.market_id, []).append(row)

    prices_by_market = {}
    for market_id, trade_rows in trades_by_market.items():
        outcomes = outcomes_by_market[market_id]
        position_of = {row.outcome_id: row.position for row in outcomes}
        try:
            replayed = price_history_of(
                trades=[_replayed_trade_of(row, position_of) for row in trade_rows],
                outcome_count=len(outcomes),
                b=outcomes[0].liquidity_b,
                opened_at=trade_rows[0].opened_at,
            )
        # ValueError: the trades skip or reorder a state version. LookupError:
        # a trade on an outcome row the book no longer holds. Either costs this
        # card its baseline, never the whole list (DECISIONS.md, "A book the
        # batch snapshot cannot price is left out and logged").
        except (ValueError, LookupError):
            logger.warning(
                "no prices at %s for market %s: its trades cannot be replayed",
                at,
                market_id,
            )
            continue
        last_point = replayed[-1]
        prices_by_market[market_id] = book_prices.priced(outcomes, list(last_point.prices))
    return prices_by_market
