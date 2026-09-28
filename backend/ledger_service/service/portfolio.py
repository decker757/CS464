"""Reading a user's whole portfolio: balance, and every position they still
hold, valued at liquidation. [T-4] #24

"The portfolio reads its balance and positions in one statement, after the
grant, with no lock" and "The portfolio carries ids only, and makes no call
to market_service" in DECISIONS.md. A position is valued the way ADR 0018
requires: `core/pricing.py::liquidation_values_of` sells a market's held
outcomes one at a time, in `outcome_position` order, each on the book the
previous sale left.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby

from sqlalchemy import and_, func, literal, select, true
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import InsufficientSharesOutstanding, MarketBookIncomplete
from core.lmsr import prices as lmsr_prices
from core.pricing import liquidation_values_of, per_share, quantize_price
from model.entities import (
    Account,
    AccountKind,
    Entry,
    MarketBook,
    MarketOutcome,
    Position,
)
from service import book_prices, grants

ZERO = Decimal(0)
_ZERO_AMOUNT = Decimal("0.0000")


@dataclass(frozen=True)
class PortfolioPosition:
    """One outcome the caller still holds, valued at liquidation (ADR 0018)."""

    market_id: uuid.UUID
    outcome_id: uuid.UUID
    outcome_position: int
    quantity: Decimal
    cost_basis: Decimal
    average_entry_price: Decimal
    price: Decimal
    value: Decimal
    unrealized_pnl: Decimal
    state_version: int


@dataclass(frozen=True)
class Portfolio:
    """A user's balance and every position they still hold."""

    user_id: uuid.UUID
    account_id: uuid.UUID
    balance: Decimal
    positions_value: Decimal
    net_worth: Decimal
    positions: list[PortfolioPosition]


async def _read(session: AsyncSession, user_id: uuid.UUID) -> tuple[Decimal, list[Row]]:
    """The balance and every outcome of every market the caller holds
    `quantity > 0` in, in one statement, with no lock.

    An anchor row carries the balance as a scalar subquery over the user's
    entries, so it survives even where the user holds nothing. It is
    left-joined to every outcome of each market the user holds a position in
    — not only the held outcome, so the market's whole `q` is one join away —
    and each outcome is joined a second time, by `(user, market, outcome)`,
    to carry `quantity` and `cost_basis` beside it (both `None` for an
    outcome the caller does not hold).
    """
    balance = (
        select(func.coalesce(func.sum(Entry.amount), ZERO))
        .join(Account, Account.id == Entry.account_id)
        .where(Account.kind == AccountKind.USER, Account.owner_id == user_id)
        .scalar_subquery()
    )
    anchor = select(literal(1).label("_anchor"), balance.label("balance")).subquery()

    held_markets = (
        select(Position.market_id)
        .where(Position.user_id == user_id, Position.quantity > 0)
        .distinct()
        .subquery()
    )

    stmt = (
        select(
            anchor.c.balance,
            MarketOutcome.market_id,
            MarketOutcome.outcome_id,
            MarketOutcome.position,
            MarketOutcome.q,
            MarketBook.liquidity_b,
            MarketBook.state_version,
            Position.quantity,
            Position.cost_basis,
        )
        .select_from(anchor)
        .outerjoin(held_markets, true())
        .outerjoin(MarketOutcome, MarketOutcome.market_id == held_markets.c.market_id)
        .outerjoin(MarketBook, MarketBook.market_id == MarketOutcome.market_id)
        .outerjoin(
            Position,
            and_(
                Position.user_id == user_id,
                Position.market_id == MarketOutcome.market_id,
                Position.outcome_id == MarketOutcome.outcome_id,
            ),
        )
        .order_by(MarketOutcome.market_id, MarketOutcome.position)
    )

    rows = (await session.execute(stmt)).all()
    balance_amount = rows[0].balance if rows else ZERO
    held = [row for row in rows if row.market_id is not None]
    return balance_amount, held


def _positions_of(rows: list[Row]) -> list[PortfolioPosition]:
    """Value every held market's outcomes and flatten them into rows with
    `quantity > 0`.

    Raises `MarketBookIncomplete` for a book `book_prices.refuse_unpriceable`
    refuses, or a holding above its outcome's `q` — "A damaged book, or a
    position above `q`, fails the whole portfolio with
    `market_book_incomplete`".
    """
    positions: list[PortfolioPosition] = []
    for _, group in groupby(rows, key=lambda row: row.market_id):
        outcomes = list(group)
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

        for outcome, held, value, price in zip(
            outcomes, holdings, values, lmsr_prices(q, b), strict=True
        ):
            if held == 0:
                continue
            cost_basis = (
                outcome.cost_basis if outcome.cost_basis is not None else ZERO
            )
            positions.append(
                PortfolioPosition(
                    market_id=outcome.market_id,
                    outcome_id=outcome.outcome_id,
                    outcome_position=outcome.position,
                    quantity=held,
                    cost_basis=cost_basis,
                    average_entry_price=per_share(cost_basis, held),
                    price=quantize_price(price),
                    value=value,
                    unrealized_pnl=value - cost_basis,
                    state_version=outcome.state_version,
                )
            )
    return positions


async def portfolio_of_user(session: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    """This user's balance and every position they still hold, valued at
    liquidation (ADR 0018). [T-4] #24

    Mints the user's starting grant first, exactly as the balance route does,
    which may post and commit. The read that follows takes no lock and makes
    no call to market_service, so a market that has closed is valued at its
    frozen book like any other.
    """
    account = await grants.ensure_granted(session, user_id)
    balance, rows = await _read(session, user_id)
    positions = _positions_of(rows)
    positions_value = sum((position.value for position in positions), _ZERO_AMOUNT)
    return Portfolio(
        user_id=user_id,
        account_id=account.id,
        balance=balance,
        positions_value=positions_value,
        net_worth=balance + positions_value,
        positions=positions,
    )
