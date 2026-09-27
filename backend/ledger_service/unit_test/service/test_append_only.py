"""`ledger.entries` cannot be changed or removed. [F-1] #41, ADR 0009.

The statement-level trigger that ships with the table refuses UPDATE, DELETE
and TRUNCATE, even from the owning role. Raw SQL on purpose: there is no ORM
path to reach for, so the database is asked directly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import NamedTuple

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Entry
from service import grants


class _Row(NamedTuple):
    """One entry, as plain values: after the rollback, reading an expired ORM
    object would refresh it and die with MissingGreenlet."""

    id: uuid.UUID
    transaction_id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal


async def _one_entry(session: AsyncSession, user_id: uuid.UUID) -> _Row:
    """The user's side of their starting grant: the credit leg, not whichever
    leg a bare `limit(1)` returns."""
    await grants.ensure_granted(session, user_id)
    row = (
        await session.execute(
            select(Entry.id, Entry.transaction_id, Entry.account_id, Entry.amount)
            .where(Entry.amount > 0)
            .limit(1)
        )
    ).one()
    return _Row(*row)


async def test_an_entry_cannot_be_updated(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    entry = await _one_entry(session, user_id)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("UPDATE ledger.entries SET amount = 1 WHERE id = :id"),
            {"id": entry.id},
        )
    await session.rollback()


async def test_an_entry_cannot_be_deleted(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    entry = await _one_entry(session, user_id)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("DELETE FROM ledger.entries WHERE id = :id"), {"id": entry.id}
        )
    await session.rollback()


async def test_the_table_cannot_be_truncated(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The one a row-level trigger would miss, and the one somebody reaches for
    when they want the table empty in a hurry."""
    await _one_entry(session, user_id)

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("TRUNCATE ledger.entries"))
    await session.rollback()


async def test_a_mutation_that_matches_no_rows_still_fails(
    session: AsyncSession,
) -> None:
    """Statement-level rather than row-level, so the refusal does not depend on
    the WHERE clause happening to find something."""
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            text("UPDATE ledger.entries SET amount = 1 WHERE id = :id"),
            {"id": uuid.uuid4()},
        )
    await session.rollback()


async def test_a_failed_mutation_leaves_the_entry_alone(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    entry = await _one_entry(session, user_id)

    with pytest.raises(DBAPIError):
        await session.execute(
            text("UPDATE ledger.entries SET amount = 1 WHERE id = :id"),
            {"id": entry.id},
        )
    await session.rollback()

    unchanged = (
        await session.execute(select(Entry.amount).where(Entry.id == entry.id))
    ).scalar_one()
    assert unchanged == entry.amount == starting_credits


async def test_appending_still_works(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The trigger covers UPDATE, DELETE and TRUNCATE and must not touch
    INSERT, or the ledger would be not so much append-only as read-only."""
    await grants.ensure_granted(session, user_id)

    count = len((await session.execute(select(Entry))).scalars().all())
    assert count == 2


async def test_a_zero_amount_entry_is_refused(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """`ck_entries_amount_nonzero`. A zero entry passes the sum-to-zero
    invariant while saying nothing, and appears on a statement as a
    transaction that did not happen."""
    entry = await _one_entry(session, user_id)

    with pytest.raises(DBAPIError, match="ck_entries_amount_nonzero"):
        await session.execute(
            text(
                "INSERT INTO ledger.entries "
                "(id, transaction_id, account_id, amount, created_at) "
                "VALUES (:id, :tx, :acct, 0, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tx": entry.transaction_id,
                "acct": entry.account_id,
            },
        )
    await session.rollback()
