"""A user's statement, and the running balance beside it. [4.1] #13

Each figure is the balance as at that entry, the newest is the balance route's
answer, and neither moves because somebody traded mid-read. Expectations are
computed from the entries themselves, not by the arithmetic under test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.paging import encode_cursor
from model.entities import Account, AccountKind, Entry, TransactionKind
from service import accounts, ledger_service, posting
from service.posting import Leg

ZERO = Decimal(0)


async def _movements(
    session: AsyncSession, user_id: uuid.UUID, amounts: list[Decimal]
) -> Account:
    """Post one movement per amount against this user, oldest first.

    Signed from the user's side. Reads the balance first, which mints the
    grant. Timestamps are injected a second apart, starting after the newest
    entry already there, so the feed order is the order written here and a
    second call appends rather than interleaves.
    """
    await ledger_service.balance_of_user(session, user_id)

    user = await accounts.ensure(session, AccountKind.USER, user_id)
    platform = await accounts.ensure_platform(session)
    base = (
        await session.execute(
            select(func.max(Entry.created_at)).where(Entry.account_id == user.id)
        )
    ).scalar_one() or datetime.now(UTC)

    for index, amount in enumerate(amounts):
        await posting.post(
            session,
            idempotency_key=f"test:{uuid.uuid4()}",
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=-amount),
                Leg(account=user, amount=amount),
            ],
            now=base + timedelta(seconds=index + 1),
        )

    return user


async def _expected(
    session: AsyncSession, account_id: uuid.UUID
) -> list[tuple[uuid.UUID, Decimal]]:
    """Every entry on the account, newest first, with the balance it left.

    Deliberately the slow way — read the lot oldest first and accumulate — so
    that it shares no code with the paged aggregate it is checking.
    """
    rows = list(
        (
            await session.execute(
                select(Entry)
                .where(Entry.account_id == account_id)
                .order_by(Entry.created_at, Entry.id)
            )
        ).scalars()
    )

    running = ZERO
    balances = []
    for entry in rows:
        running += entry.amount
        balances.append((entry.id, running))

    return list(reversed(balances))


def _as_pairs(rows: list[ledger_service.HistoryRow]) -> list[tuple[uuid.UUID, Decimal]]:
    return [(row.entry.id, row.balance_after) for row in rows]


async def _walk(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int
) -> list[ledger_service.HistoryRow]:
    """Every page, in order, the way a 'load more' control would."""
    rows: list[ledger_service.HistoryRow] = []
    cursor: str | None = None

    while True:
        page = await ledger_service.history_for_user(
            session, user_id, limit=limit, cursor=cursor
        )
        rows.extend(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            return rows


# --- the running balance --------------------------------------------------


async def test_each_entry_carries_the_balance_it_left_behind(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """[4.1] #13's second criterion. Newest first, and the figure beside an
    entry is the account as it stood the moment that entry landed."""
    account = await _movements(
        session, user_id, [Decimal("-250"), Decimal("100"), Decimal("-75")]
    )

    page = await ledger_service.history_for_user(session, user_id, limit=10)

    assert _as_pairs(page.rows) == await _expected(session, account.id)


async def test_the_newest_running_balance_is_the_balance_route_s_answer(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """[4.1] #13's third criterion, holding by construction rather than by
    reconciliation: both numbers are SUM(amount) over the same rows, so there
    is no second source for the statement and the balance to drift apart."""
    await _movements(session, user_id, [Decimal("-250"), Decimal("100")])

    page = await ledger_service.history_for_user(session, user_id, limit=10)
    balance = await ledger_service.balance_of_user(session, user_id)

    assert page.rows[0].balance_after == balance.amount


async def test_a_debit_lowers_the_balance_it_leaves(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The direction of the walk: a wrong one still produces a plausible
    column of numbers."""
    await _movements(session, user_id, [Decimal("-250")])

    page = await ledger_service.history_for_user(session, user_id, limit=10)
    debit, grant = page.rows

    assert debit.entry.amount == Decimal("-250.0000")
    assert debit.balance_after == grant.balance_after + debit.entry.amount


async def test_a_history_with_only_the_grant_shows_the_grant(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """The first thing an administrator sees on a brand new account, and the
    one page that exists before anybody has traded."""
    page = await ledger_service.history_for_user(session, user_id, limit=10)

    assert [row.balance_after for row in page.rows] == [starting_credits]


# --- paging ---------------------------------------------------------------


async def test_the_running_balance_continues_across_pages(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Page 2 picks up where page 1 left off, rather than restarting from its
    own newest entry."""
    account = await _movements(
        session,
        user_id,
        [Decimal("-250"), Decimal("100"), Decimal("-75"), Decimal("40")],
    )

    assert _as_pairs(await _walk(session, user_id, limit=2)) == await _expected(
        session, account.id
    )


async def test_paging_agrees_with_reading_it_all_at_once(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A page size is a rendering decision and must not be an arithmetic one."""
    await _movements(
        session, user_id, [Decimal("-250"), Decimal("100"), Decimal("-75")]
    )

    whole = await ledger_service.history_for_user(session, user_id, limit=50)

    assert _as_pairs(await _walk(session, user_id, limit=1)) == _as_pairs(whole.rows)


async def test_a_page_past_the_end_is_empty(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """No rows, and so no newest entry to anchor an aggregate on: a stale or
    hand-built cursor comes back empty rather than raising."""
    await _movements(session, user_id, [Decimal("-250")])

    oldest = (
        await ledger_service.history_for_user(session, user_id, limit=50)
    ).rows[-1].entry

    past_the_end = await ledger_service.history_for_user(
        session, user_id, limit=2, cursor=encode_cursor(oldest.created_at, oldest.id)
    )

    assert past_the_end.rows == []
    assert past_the_end.has_more is False


# --- stability ------------------------------------------------------------


async def test_a_movement_arriving_mid_read_does_not_move_an_earlier_figure(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Why the sum is anchored to a position: counting back from the live
    balance would shift every figure on page 2 by a trade made after page 1.
    """
    account = await _movements(
        session, user_id, [Decimal("-250"), Decimal("100"), Decimal("-75")]
    )

    before = await _expected(session, account.id)
    page_one = await ledger_service.history_for_user(session, user_id, limit=2)

    await _movements(session, user_id, [Decimal("-500")])

    page_two = await ledger_service.history_for_user(
        session, user_id, limit=2, cursor=page_one.next_cursor
    )

    # The rows below the cursor are the ones page 1 promised were coming, at
    # the balances they had before the new movement existed.
    assert _as_pairs(page_one.rows) + _as_pairs(page_two.rows) == before

    # A fresh read, by contrast, shows the new movement on top.
    fresh = await ledger_service.history_for_user(session, user_id, limit=2)
    assert _as_pairs(fresh.rows) == (await _expected(session, account.id))[:2]


# --- the design this rests on ---------------------------------------------


def test_the_running_balance_is_not_a_column() -> None:
    """`ledger.entries` must never grow one: a stored balance would be a second
    definition a concurrent write could make disagree. ADR 0009."""
    stored = {column.name for column in Entry.__table__.columns}

    assert not {name for name in stored if "balance" in name}
