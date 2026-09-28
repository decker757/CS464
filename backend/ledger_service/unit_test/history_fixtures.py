"""Fixtures the [T-5] #25 test files share.

Built on `sell_fixtures.py`, `portfolio_fixtures.py` and `trade_fixtures.py`
rather than beside them: the same `Upstream`, the same `market_at`, `funded`,
`hold` and `sell`, the same lazy module handles. What is added here is the
driver for the history read, the five trade fields, the worked example as one
scenario, and the one-leg guard's query.

**The interface is agreed for #25, and this is the single place it is written
down.** `ledger_service.history_for_user(session, user_id, *, limit, cursor)`
returns an `EntryPage` as it did for [4.1] #13. Each `HistoryRow` keeps `entry`
and `balance_after`, and gains `market_id`, `outcome_id`, `side`, `quantity`
and `average_price`, all nullable and defaulting to `None`. `LedgerEntryOut`
gains the same five on the wire.

**History comes from real grants and trades on a book opened at zero.** The
numbers are #23's worked example at `b = 137`: buying 54.3333 costs 29.8428,
and selling 10 of it pays 5.8905. The sell is sent as `"10"`, not `"10.0000"`,
so the row's quantity has to be quantized to show at scale 4.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.portfolio_fixtures import funded, market_at
from unit_test.sell_fixtures import BASIS, HELD, PROCEEDS, hold, sell
from unit_test.trade_fixtures import (
    QUANTUM,
    Upstream,
    entities,
    grants,
    posting,
)

# --- the worked example, pinned ------------------------------------------
BUY_COST = BASIS  # 29.8428: the buy's amount is its negative
SENT_AS_TEN = Decimal("10")  # the sell, as the trader typed it

# 29.8428 / 54.3333 = 0.549254…: half-up 0.5493, floor 0.5492.
BUY_AVERAGE = Decimal("0.5493")
# 5.8905 / 10 = 0.58905 exactly: half-up 0.5891, half-even and floor 0.5890.
SELL_AVERAGE = Decimal("0.5891")

TRADE_FIELDS = ("market_id", "outcome_id", "side", "quantity", "average_price")
EXISTING_FIELDS = {
    "id",
    "created_at",
    "amount",
    "balance_after",
    "transaction_id",
    "kind",
    "context",
}


def assert_average_rounding_is_load_bearing() -> None:
    """A property of the constants, not of the code under test: each
    average differs from what a wrong rounding mode would give, so pinning it
    pins the mode."""
    buy = BUY_COST / HELD
    assert buy.quantize(QUANTUM, rounding=ROUND_HALF_UP) == BUY_AVERAGE
    assert buy.quantize(QUANTUM, rounding=ROUND_FLOOR) != BUY_AVERAGE

    sell_average = PROCEEDS / SENT_AS_TEN
    assert sell_average.quantize(QUANTUM, rounding=ROUND_HALF_UP) == SELL_AVERAGE
    assert sell_average.quantize(QUANTUM, rounding=ROUND_HALF_EVEN) != SELL_AVERAGE
    assert sell_average.quantize(QUANTUM, rounding=ROUND_FLOOR) != SELL_AVERAGE


# --- lazy handles --------------------------------------------------------
def ledger_service():
    from service import ledger_service  # noqa: PLC0415

    return ledger_service


# --- driving the thing under test ----------------------------------------
async def read_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
):
    return await ledger_service().history_for_user(
        session, user_id, limit=limit, cursor=cursor
    )


async def walk(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int
) -> list[list]:
    """Every page, in order, the way a 'load more' control would."""
    pages = []
    cursor: str | None = None
    while True:
        page = await read_history(session, user_id, limit=limit, cursor=cursor)
        pages.append(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            return pages


def trade_fields(row) -> tuple:
    """The five fields #25 adds, in `TRADE_FIELDS` order."""
    return tuple(getattr(row, name) for name in TRADE_FIELDS)


def transaction_ids(rows) -> list[uuid.UUID]:
    return [row.entry.transaction_id for row in rows]


# --- the scenario ----------------------------------------------------------
@dataclass(frozen=True)
class Traded:
    """A user granted, then the worked example's buy and sell."""

    upstream: Upstream
    user_id: uuid.UUID
    credits: Decimal
    grant_id: uuid.UUID
    buy: object
    sell: object

    @property
    def after_buy(self) -> Decimal:
        return self.credits - BUY_COST

    @property
    def after_sell(self) -> Decimal:
        return self.credits - BUY_COST + PROCEEDS


async def grant_id_of(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    transaction = await posting().find_by_idempotency_key(
        session, grants().grant_key(user_id)
    )
    assert transaction is not None, "the user was never granted"
    return transaction.id


async def traded(session: AsyncSession) -> Traded:
    """A market at `b = 137` opened at zero, a funded user, 54.3333 of
    outcome 0 bought and then 10 of it sold, sent as `"10"`."""
    upstream = await market_at(session)
    user_id, credits = await funded(session)
    bought = await hold(session, upstream, user_id=user_id)
    sold = await sell(session, upstream, user_id=user_id, quantity=SENT_AS_TEN)
    return Traded(
        upstream=upstream,
        user_id=user_id,
        credits=credits,
        grant_id=await grant_id_of(session, user_id),
        buy=bought,
        sell=sold,
    )


# --- reading the ledger directly --------------------------------------------
async def user_entries(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[uuid.UUID, uuid.UUID, Decimal]]:
    """`(entry id, transaction id, amount)` for every entry on this user's
    account, newest first, straight from the table."""
    ents = entities()
    rows = (
        await session.execute(
            select(ents.Entry.id, ents.Entry.transaction_id, ents.Entry.amount)
            .join(ents.Account, ents.Account.id == ents.Entry.account_id)
            .where(
                ents.Account.kind == ents.AccountKind.USER,
                ents.Account.owner_id == user_id,
            )
            .order_by(ents.Entry.created_at.desc(), ents.Entry.id.desc())
        )
    ).all()
    return [(entry_id, transaction_id, amount) for entry_id, transaction_id, amount in rows]


async def expected_balances(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[uuid.UUID, Decimal]]:
    """`(entry id, balance after)` newest first, accumulated oldest first from
    the table, so it shares no code with the read it checks."""
    running = Decimal(0)
    oldest_first = []
    for entry_id, _, amount in reversed(await user_entries(session, user_id)):
        running += amount
        oldest_first.append((entry_id, running))
    return list(reversed(oldest_first))


def as_pairs(rows) -> list[tuple[uuid.UUID, Decimal]]:
    return [(row.entry.id, row.balance_after) for row in rows]


async def stored_context(session: AsyncSession, transaction_id: uuid.UUID) -> dict:
    transaction = entities().Transaction
    return (
        await session.execute(
            select(transaction.context).where(transaction.id == transaction_id)
        )
    ).scalar_one()


# --- the one-leg guard -------------------------------------------------------
async def transactions_with_two_legs_on_one_user_account(
    session: AsyncSession,
) -> list[uuid.UUID]:
    """"A transaction puts at most one leg on any USER account": every
    transaction that breaks it, over the whole ledger."""
    ents = entities()
    rows = (
        await session.execute(
            select(ents.Entry.transaction_id)
            .join(ents.Account, ents.Account.id == ents.Entry.account_id)
            .where(ents.Account.kind == ents.AccountKind.USER)
            .group_by(ents.Entry.transaction_id, ents.Entry.account_id)
            .having(func.count() > 1)
        )
    ).scalars()
    return list(rows)
