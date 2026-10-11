"""Reading a user's whole portfolio: balance, and every position they still
hold, valued at liquidation. [T-4] #24, [3.4] #12

"The portfolio reads its balance and positions in one statement, after the
grant, with no lock" and "The portfolio carries ids only, and makes no call
to market_service" in DECISIONS.md. A position is valued the way ADR 0018
requires: `core/pricing.py::liquidation_values_of` sells a market's held
outcomes one at a time, in `outcome_position` order, each on the book the
previous sale left.

The valuation itself, settled branch included, is `service/valuation.py`'s,
shared with the leaderboard refresh.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, literal, select, true
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Account, AccountKind, Entry, MarketOutcome, Position
from service import grants, valuation

ZERO = Decimal(0)


@dataclass(frozen=True)
class Portfolio:
    """A user's balance and every position they still hold."""

    user_id: uuid.UUID
    account_id: uuid.UUID
    balance: Decimal
    positions_value: Decimal
    net_worth: Decimal
    positions: list[valuation.PortfolioPosition]


async def _read(session: AsyncSession, user_id: uuid.UUID) -> tuple[Decimal, list[Row]]:
    """The balance and every outcome of every market the caller holds
    `quantity > 0` in, in one statement, with no lock.

    The anchor row carries the balance as a scalar subquery over the user's
    entries, so it survives even where the user holds nothing. `held_markets`
    keeps the markets with a position of `quantity > 0`. The joins from there
    are `valuation.held_outcomes_query`'s.
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

    anchored = select(anchor.c.balance).select_from(anchor).outerjoin(held_markets, true())
    stmt = valuation.held_outcomes_query(anchored, held_markets, holder_id=user_id).order_by(
        MarketOutcome.market_id, MarketOutcome.position
    )

    rows = (await session.execute(stmt)).all()
    balance_amount = rows[0].balance if rows else ZERO
    held = [row for row in rows if row.market_id is not None]
    return balance_amount, held


async def portfolio_of_user(session: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    """This user's balance and every position they still hold, valued at
    liquidation (ADR 0018). [T-4] #24

    Mints the user's starting grant first, exactly as the balance route does,
    which may post and commit. The read that follows takes no lock and makes
    no call to market_service, so a market that has closed is valued at its
    frozen book like any other. A settled market is not valued: its payout is
    already in `balance`, so its rows add nothing to `positions_value`.
    """
    account = await grants.ensure_granted(session, user_id)
    balance, rows = await _read(session, user_id)
    totals = valuation.valuation_of(balance, rows)
    return Portfolio(
        user_id=user_id,
        account_id=account.id,
        balance=balance,
        positions_value=totals.positions_value,
        net_worth=totals.net_worth,
        positions=totals.positions,
    )
