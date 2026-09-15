"""`ledger.entries` cannot be changed or removed. [F-1] #41

The API surface is the absence of any function that mutates an entry. This is
the enforcement: a statement-level trigger, installed with the table by an
after_create DDL event in `model/entities.py`, that refuses UPDATE, DELETE and
TRUNCATE — including from the role that owns the table.

Every test here writes raw SQL on purpose. There is no ORM path to reach for,
which is the point, so the only way to prove the database refuses is to ask the
database directly.
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
    """One entry, as plain values rather than as an ORM object.

    Every test below rolls the session back after the database refuses a
    mutation, and a rollback expires every loaded instance. Reading an
    attribute off one afterwards makes SQLAlchemy refresh it, which is IO in a
    place asyncio cannot do it, so the test dies with MissingGreenlet instead
    of asserting what it came to assert.
    """

    id: uuid.UUID
    transaction_id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal


async def _one_entry(session: AsyncSession, user_id: uuid.UUID) -> _Row:
    """The user's side of their starting grant.

    Filtered to the credit leg rather than taken with a bare `limit(1)`, which
    would return whichever of the two legs Postgres felt like and make every
    assertion about the amount a coin toss.
    """
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
