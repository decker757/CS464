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
column so the statement count does not move. And for #410, what each held
outcome's live sell orders hold back, read as one more column of the same
statement, so a fill committing mid-read cannot show the position on one side
of it and the reservation on the other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, func, literal, select, true
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import QUANTUM
from model.entities import (
    Account,
    AccountKind,
    Entry,
    MarketOutcome,
    Position,
    Transaction,
)
from service import grants, reservations, valuation, voiding

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
    # Keyed `(market_id, outcome_id)`, one entry per outcome of every held
    # market: the shares the user's live sell orders hold back (#410).
    reserved_shares: dict[tuple[uuid.UUID, uuid.UUID], Decimal]


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
    Each outcome row also carries `reserved_shares`, a subquery over the
    caller's live sell orders of that outcome, correlated to the outcome, and
    `void_refund`, the caller's leg of that outcome's `void_refund`, found by
    its derived key on the unique `idempotency_key`, or null where there is
    none ([BE] #221, ruling A). Not in `held_outcomes_query`, so the
    leaderboard's refresh reads no refund.
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
    reserved_shares = reservations.reserved_shares_query_of(
        user_id,
        market_id=MarketOutcome.market_id,
        outcome_id=MarketOutcome.outcome_id,
    ).scalar_subquery()
    void_refund = (
        select(Entry.amount)
        .join(Account, Account.id == Entry.account_id)
        .join(Transaction, Transaction.id == Entry.transaction_id)
        .where(
            Account.kind == AccountKind.USER,
            Account.owner_id == user_id,
            Transaction.idempotency_key
            == voiding.refund_key_column(
                MarketOutcome.market_id,
                user_id=user_id,
                outcome_id=MarketOutcome.outcome_id,
            ),
        )
        .scalar_subquery()
    )
    stmt = (
        valuation.held_outcomes_query(anchored, held_markets, holder_id=user_id)
        .add_columns(
            reserved_shares.label("reserved_shares"),
            void_refund.label("void_refund"),
        )
        .order_by(MarketOutcome.market_id, MarketOutcome.position)
    )

    # Never empty: the anchor selects from nothing, so it is always one row,
    # and every join below it is a left join.
    rows = (await session.execute(stmt)).all()
    return rows[0].balance, rows[0].starting_credits, rows


def _refunds_of(rows: list[Row]) -> valuation.Refunds:
    """Each held outcome's refund from `_read`'s rows, keyed `(market_id,
    outcome_id)`, for the outcomes a void refunded this user."""
    refunds: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for row in rows:
        if row.market_id is None or row.void_refund is None:
            continue
        refunds[(row.market_id, row.outcome_id)] = row.void_refund
    return refunds


def _reserved_shares_of(rows: list[Row]) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    """Each outcome's `reserved_shares` from `_read`'s rows, keyed
    `(market_id, outcome_id)`, at scale 4.

    The anchor row, joined to no market, is skipped. The quantize is for an
    outcome no order matched, where `coalesce` answers a bare `0`.
    """
    reserved: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for row in rows:
        if row.market_id is None:
            continue
        reserved[(row.market_id, row.outcome_id)] = row.reserved_shares.quantize(QUANTUM)
    return reserved


async def portfolio_of_user(session: AsyncSession, user_id: uuid.UUID) -> Portfolio:
    """This user's balance, every position they still hold valued at
    liquidation (ADR 0018), what both come to against their grant, and the
    shares their live sell orders hold back. [T-4] #24, [B-1] #359, #410

    Mints the user's starting grant first, exactly as the balance route does,
    which may post and commit. The read that follows takes no lock and makes
    no call to market_service, so a market that has closed is valued at its
    frozen book like any other. A settled or voided market is not valued: its
    payout or refund is already in `balance`, so its rows add nothing to
    `positions_value`.
    """
    account = await grants.ensure_granted(session, user_id)
    balance, starting_credits, rows = await _read(session, user_id)
    totals = valuation.valuation_of(balance, rows, refunds=_refunds_of(rows))
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
        reserved_shares=_reserved_shares_of(rows),
    )
