"""The read model against the table it describes. [4.3] #15

This service does not own `audit.admin_actions`. `sql/02-schemas.sql` creates
it, and `model/entities.py` is a hand-written description of somebody else's
table with no `create_all` to reconcile the two. Nothing but this file stops
them drifting until [F-5] #75 brings Alembic.

Drift is not hypothetical here. A column added to the SQL for a new action type
would be invisible to every query this service runs, and a column renamed in
the SQL would fail every one of them at runtime with `column ... does not
exist` — the same failure mode the repository README already warns about for
`create_all`, arriving from the opposite direction.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import AdminAction

_COLUMNS = text(
    "SELECT column_name, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'audit' AND table_name = 'admin_actions'"
)


async def test_the_model_names_every_column_the_table_has(
    session: AsyncSession,
) -> None:
    """A column in the SQL and not in the model is one no query can ever read."""
    in_database = {
        row.column_name for row in (await session.execute(_COLUMNS)).mappings()
    }
    in_model = {column.name for column in AdminAction.__table__.columns}

    assert in_database - in_model == set()


async def test_the_model_invents_no_column_the_table_lacks(
    session: AsyncSession,
) -> None:
    """And one in the model but not the SQL fails every query at runtime."""
    in_database = {
        row.column_name for row in (await session.execute(_COLUMNS)).mappings()
    }
    in_model = {column.name for column in AdminAction.__table__.columns}

    assert in_model - in_database == set()


async def test_the_model_agrees_about_what_may_be_null(
    session: AsyncSession,
) -> None:
    """The nullability is a design decision, not an accident.

    Only the fields a writer always knows are NOT NULL, because the audit
    INSERT commits with the action it records: a column this table can reject
    on is a way for the log to abort a legitimate admin action. If the SQL and
    the model disagree, one of them has changed without the other.
    """
    nullable_in_database = {
        row.column_name
        for row in (await session.execute(_COLUMNS)).mappings()
        if row.is_nullable == "YES"
    }
    nullable_in_model = {
        column.name for column in AdminAction.__table__.columns if column.nullable
    }

    assert nullable_in_database == nullable_in_model


@pytest.mark.parametrize(
    "column", ["occurred_at", "actor_id", "actor_username", "actor_role", "action_type"]
)
async def test_the_fields_the_story_requires_are_mandatory(
    session: AsyncSession, column: str
) -> None:
    """[4.3] #15's first criterion: actor, action type, target and timestamp on
    every entry. An entry that could omit the actor would be an entry that
    fails the one thing the log exists for."""
    is_nullable = {
        row.column_name: row.is_nullable
        for row in (await session.execute(_COLUMNS)).mappings()
    }

    assert is_nullable[column] == "NO"
