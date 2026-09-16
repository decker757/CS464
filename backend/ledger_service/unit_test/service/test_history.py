"""A user's statement, and the running balance beside it. [4.1] #13

The story is an administrator investigating an anomaly, so what matters is not
that a page comes back but that the numbers on it can be trusted: each figure
is the balance as at that entry, the newest one is the balance the balance
route reports, and neither changes because somebody traded while the page was
being read.

Every expectation below is computed independently, from the entries themselves,
rather than by re-running the arithmetic under test.
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

    Timestamps are injected a second apart rather than left to the clock, so
    the feed order is the order written here and an assertion about "the entry
    below this one" means something. Signed from the user's point of view:
    negative takes credits out of their account and into the platform's.

    Reads the balance first, which is what mints the starting grant — so every
    history below opens on a funded account, exactly as a real one does, and
    the debits have something to come out of.

    The clock starts from the newest entry already on the account rather than
    from now, so that a second call appends to the first call's history instead
    of interleaving with it. Injected timestamps are seconds ahead of real time
    and a fresh `now()` would land underneath them.
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
    """The direction of the walk, stated on its own.

    Off by one in the subtraction, or a walk in the wrong direction, still
    produces a plausible column of numbers. This is the assertion that says
    which way time runs.
    """
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
    """Page 2 picks up where page 1 left off.

    The failure this guards against is a per-page balance that restarts from
    the newest entry of whichever page is being rendered, which looks right on
    page 1 and is wrong everywhere after it.
    """
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
    """No rows, and so no newest entry to anchor an aggregate on.

    Not reachable by following `next_cursor`, which is exact — this is the
    client that kept a cursor from yesterday, or built one. It has to come back
    empty rather than raise on the first element of an empty page.
    """
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
    """The reason the sum is anchored to a position rather than counted back
    from today's balance.

    An administrator opens page 1, the user trades, the administrator asks for
    page 2. Counting back from the live balance would shift every figure below
    the new entry by its amount, and the statement would stop adding up in the
    middle. Anchoring puts the new entry outside every sum on the pages already
    fetched.
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


# --- the design this rests on ---------------------------------------------


def test_the_running_balance_is_not_a_column() -> None:
    """`ledger.entries` must never grow one. [4.1] #13's third criterion is
    true because a balance has exactly one definition; a stored `balance_after`
    would be a second, and a concurrent write is all it takes to make the two
    disagree. The entity docstring says so; this fails if somebody adds it
    anyway."""
    stored = {column.name for column in Entry.__table__.columns}

    assert not {name for name in stored if "balance" in name}
