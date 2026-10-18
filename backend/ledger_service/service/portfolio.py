"""Reading a user's whole portfolio: balance, every position they still
hold valued at liquidation, and what the two come to against the grant.
[T-4] #24, [3.4] #12, [B-1] #359

"The portfolio reads its balance and positions in one statement, after the
grant, with no lock" and "The portfolio carries ids only, and makes no call
to market_service" in DECISIONS.md. A position is valued the way ADR 0018
requires: `core/pricing.py::liquidation_values_of` sells a market's held
outcomes one at a time, in `outcome_position` order, each on the book the
previous sale left.

The valuation itself, settled branch included, is `service/valuation.py`'s,
shared with the leaderboard refresh, and so is the profit and loss
arithmetic. What this file adds for #359 is the one thing neither of them
can see: the grant this user was actually given, read as a second anchor
column so the statement count does not move.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, func, literal, select, true
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import (
    Account,
    AccountKind,
    Entry,
    MarketOutcome,
    Position,
    Transaction,
)
from service import grants, valuation

ZERO = Decimal(0)


@dataclass(frozen=True)
class Portfolio:
    """A user's balance, every position they still hold, and their P&L."""

    user_id: uuid.UUID
    account_id: uuid.UUID
    balance: Decimal
    positions_value: Decimal
    net_worth: Decimal
    starting_credits: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    positions: list[valuation.PortfolioPosition]


def _sum_of_user_entries(user_id: uuid.UUID) -> Select:
    """`SUM(amount)` over this user's own entries, for a caller to narrow
    further. Zero where the user has none, so it answers for an account that
    does not exist yet."""
    return (
        select(func.coalesce(func.sum(Entry.amount), ZERO))
        .join(Account, Account.id == Entry.account_id)
        .where(Account.kind == AccountKind.USER, Account.owner_id == user_id)
    )


async def _read(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[Decimal, Decimal, list[Row]]:
    """The balance, the starting grant, and every outcome of every market the
    caller holds `quantity > 0` in, in one statement, with no lock.

    The anchor row carries both figures as scalar subqueries over the user's
    entries, so they survive even where the user holds nothing. The grant is
    the leg of the transaction `grants.grant_key` names, not
    `STARTING_CREDITS`: the setting can change and never rewrites an entry
    (ADR 0009). `held_markets` keeps the markets with a position of
    `quantity > 0`. The joins from there are `valuation.held_outcomes_query`'s.
    """
    balance = _sum_of_user_entries(user_id).scalar_subquery()
    starting_credits = (
        _sum_of_user_entries(user_id)
        .join(Transaction, Transaction.id == Entry.transaction_id)
        .where(Transaction.idempotency_key == grants.grant_key(user_id))
        .scalar_subquery()
    )
    anchor = select(
        literal(1).label("_anchor"),
        balance.label("balance"),
        starting_credits.label("starting_credits"),
    ).subquery()

    held_markets = (
        select(Position.market_id)
        .where(Position.user_id == user_id, Position.quantity > 0)
        .distinct()
        .subquery()
    )

    anchored = (
        select(anchor.c.balance, anchor.c.starting_credits)
        .select_from(anchor)
        .outerjoin(held_markets, true())
    )
    stmt = valuation.held_outcomes_query(anchored, held_markets, holder_id=user_id).order_by(
        MarketOutcome.market_id, MarketOutcome.position
    )

    rows = (await session.execute(stmt)).all()
    if not rows:
        return ZERO, ZERO, rows
    return rows[0].balance, rows[0].starting_credits, rows


async def portfolio_of_user(session: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    """This user's balance, every position they still hold valued at
    liquidation (ADR 0018), and what both come to against their grant.
    [T-4] #24, [B-1] #359

    Mints the user's starting grant first, exactly as the balance route does,
    which may post and commit. The read that follows takes no lock and makes
    no call to market_service, so a market that has closed is valued at its
    frozen book like any other. A settled market is not valued: its payout is
    already in `balance`, so its rows add nothing to `positions_value`.
    """
    account = await grants.ensure_granted(session, user_id)
    balance, starting_credits, rows = await _read(session, user_id)
    totals = valuation.valuation_of(balance, rows)
    pnl = valuation.profit_and_loss_of(
        totals.positions, balance=balance, starting_credits=starting_credits
    )
    return Portfolio(
        user_id=user_id,
        account_id=account.id,
        balance=balance,
        positions_value=totals.positions_value,
        net_worth=totals.net_worth,
        starting_credits=pnl.starting_credits,
        realized_pnl=pnl.realized_pnl,
        unrealized_pnl=pnl.unrealized_pnl,
        total_pnl=pnl.total_pnl,
        positions=totals.positions,
    )
