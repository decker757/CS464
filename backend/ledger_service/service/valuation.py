"""Valuing one user's holdings from the rows the portfolio reads: the settled
branch, the liquidation branch, and the totals. [T-4] #24, [3.4] #12, [L-1] #38

ADR 0018 and its 2026-10-10 amendment: the leaderboard values holders with
this code, not with `liquidation_values_of` alone, so a settled market's
shares are never valued on top of their payout. DECISIONS.md, "The refresh
values holders through the portfolio's valuation, and the portfolio keeps its
one-statement read".

A market this ledger has settled is not valued at all: its rows carry what
they were paid, and the payout is already in the balance. DECISIONS.md, "A
settled row's payout is its quantity, and its result comes from
`ledger.market_results`, joined in the portfolio's one statement".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby

from sqlalchemy import ColumnElement, Select, Subquery, and_
from sqlalchemy.engine import Row

from core.errors import InsufficientSharesOutstanding, MarketBookIncomplete
from core.lmsr import prices as lmsr_prices
from core.pricing import liquidation_values_of, per_share, quantize_price
from model.entities import MarketBook, MarketOutcome, MarketResult, Position
from model.schemas import PositionResult
from service import book_prices

ZERO = Decimal(0)
_ZERO_AMOUNT = Decimal("0.0000")


@dataclass(frozen=True)
class PortfolioPosition:
    """One outcome the caller still holds.

    Valued at liquidation (ADR 0018) when its market is not settled, and then
    `result` and `payout` are None. On a settled market it is the other way
    round: `price`, `value` and `unrealized_pnl` are None.
    """

    market_id: uuid.UUID
    outcome_id: uuid.UUID
    outcome_position: int
    quantity: Decimal
    cost_basis: Decimal
    average_entry_price: Decimal
    price: Decimal | None
    value: Decimal | None
    unrealized_pnl: Decimal | None
    result: PositionResult | None
    payout: Decimal | None
    state_version: int


@dataclass(frozen=True)
class Valuation:
    """One user's held positions, and what they add to the balance."""

    positions: list[PortfolioPosition]
    positions_value: Decimal
    net_worth: Decimal


def _position_of(
    outcome: Row,
    held: Decimal,
    *,
    price: Decimal | None,
    value: Decimal | None,
    result: PositionResult | None,
    payout: Decimal | None,
) -> PortfolioPosition:
    """One held outcome's row. The cost figures are the same either side of
    settlement, so they are worked out here once."""
    cost_basis = outcome.cost_basis if outcome.cost_basis is not None else ZERO
    return PortfolioPosition(
        market_id=outcome.market_id,
        outcome_id=outcome.outcome_id,
        outcome_position=outcome.position,
        quantity=held,
        cost_basis=cost_basis,
        average_entry_price=per_share(cost_basis, held),
        price=price,
        value=value,
        unrealized_pnl=value - cost_basis if value is not None else None,
        result=result,
        payout=payout,
        state_version=outcome.state_version,
    )


def _settled_positions_of(outcomes: list[Row]) -> list[PortfolioPosition]:
    """The held outcomes of one settled market: what each was paid, and no value.

    The payout is the quantity on the winning outcome and nothing on any
    other. The book is not read, so a damaged one cannot fail the portfolio
    over figures that never reach it.
    """
    positions: list[PortfolioPosition] = []
    for outcome in outcomes:
        held = outcome.quantity if outcome.quantity is not None else ZERO
        if held == 0:
            continue
        if outcome.outcome_id == outcome.winning_outcome_id:
            result, payout = PositionResult.PAID_OUT, held
        else:
            result, payout = PositionResult.WORTHLESS, _ZERO_AMOUNT
        positions.append(
            _position_of(outcome, held, price=None, value=None, result=result, payout=payout)
        )
    return positions


def _valued_positions_of(outcomes: list[Row]) -> list[PortfolioPosition]:
    """The held outcomes of one unsettled market, valued at liquidation.

    Raises `MarketBookIncomplete` for a book `book_prices.refuse_unpriceable`
    refuses, or a holding above its outcome's `q` — "A damaged book, or a
    position above `q`, fails the whole portfolio with
    `market_book_incomplete`".
    """
    b = outcomes[0].liquidity_b
    book_prices.refuse_unpriceable(len(outcomes), b)

    q = [outcome.q for outcome in outcomes]
    holdings = [
        outcome.quantity if outcome.quantity is not None else ZERO
        for outcome in outcomes
    ]
    try:
        values = liquidation_values_of(q, b, holdings)
    except InsufficientSharesOutstanding as exc:
        raise MarketBookIncomplete from exc

    positions: list[PortfolioPosition] = []
    for outcome, held, value, price in zip(
        outcomes, holdings, values, lmsr_prices(q, b), strict=True
    ):
        if held == 0:
            continue
        positions.append(
            _position_of(
                outcome,
                held,
                price=quantize_price(price),
                value=value,
                result=None,
                payout=None,
            )
        )
    return positions


def _positions_of(rows: list[Row]) -> list[PortfolioPosition]:
    """Every held market's outcomes as rows with `quantity > 0`: a market with
    a `market_results` record is read as paid, any other is valued.

    Whether a market is settled is the record's presence, never its `kind`
    (DECISIONS.md, "A settled row's payout is its quantity").
    """
    positions: list[PortfolioPosition] = []
    for _, group in groupby(rows, key=lambda row: row.market_id):
        outcomes = list(group)
        if outcomes[0].settled_kind is not None:
            positions.extend(_settled_positions_of(outcomes))
        else:
            positions.extend(_valued_positions_of(outcomes))
    return positions


def held_outcomes_query(
    anchored: Select,
    held_markets: Subquery,
    *,
    holder_id: uuid.UUID | ColumnElement[uuid.UUID],
) -> Select:
    """`anchored` with every outcome of each market in `held_markets` joined
    on, carrying the columns `valuation_of` reads.

    `anchored` is the caller's FROM so far, already joined to `held_markets`;
    its own columns stay first. Each outcome is left-joined to its book, its
    market's `market_results` record, and `holder_id`'s position in it, so
    `quantity`, `cost_basis`, `settled_kind` and `winning_outcome_id` are
    `None` where there is nothing to join. The caller adds the ORDER BY.
    """
    return (
        anchored.add_columns(
            MarketOutcome.market_id,
            MarketOutcome.outcome_id,
            MarketOutcome.position,
            MarketOutcome.q,
            MarketBook.liquidity_b,
            MarketBook.state_version,
            MarketResult.kind.label("settled_kind"),
            MarketResult.outcome_id.label("winning_outcome_id"),
            Position.quantity,
            Position.cost_basis,
        )
        .outerjoin(MarketOutcome, MarketOutcome.market_id == held_markets.c.market_id)
        .outerjoin(MarketBook, MarketBook.market_id == MarketOutcome.market_id)
        .outerjoin(MarketResult, MarketResult.market_id == MarketOutcome.market_id)
        .outerjoin(
            Position,
            and_(
                Position.user_id == holder_id,
                Position.market_id == MarketOutcome.market_id,
                Position.outcome_id == MarketOutcome.outcome_id,
            ),
        )
    )


def valuation_of(balance: Decimal, rows: list[Row]) -> Valuation:
    """One user's positions and totals, from their balance and their rows of
    `held_outcomes_query`, ordered by `(market_id, position)`.

    Raises `MarketBookIncomplete` for an unsettled market whose book cannot
    be priced or that the user holds above `q`. A settled market is not
    valued: its payout is already in `balance`, so its rows add nothing to
    `positions_value`.
    """
    positions = _positions_of(rows)
    positions_value = sum(
        (position.value for position in positions if position.value is not None),
        _ZERO_AMOUNT,
    )
    return Valuation(
        positions=positions,
        positions_value=positions_value,
        net_worth=balance + positions_value,
    )
