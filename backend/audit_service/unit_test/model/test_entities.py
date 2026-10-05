"""The read model against the table it describes. [4.3] #15

`model/entities.py` hand-describes a table sql/02-schemas.sql owns, and only
this file stops the two drifting; ADR 0020 left that table in sql/.
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
_MODEL_COLUMNS = frozenset(column.name for column in AdminAction.__table__.columns)


async def _nullability_by_column(session: AsyncSession) -> dict[str, str]:
    """Each column the table has, mapped to its `is_nullable` ('YES' or 'NO')."""
    rows = (await session.execute(_COLUMNS)).mappings()
    return {row.column_name: row.is_nullable for row in rows}


async def test_the_model_names_every_column_the_table_has(
    session: AsyncSession,
) -> None:
    """A column in the SQL and not in the model is one no query can ever read."""
    columns = await _nullability_by_column(session)
    in_database = set(columns)
    in_model = _MODEL_COLUMNS

    assert in_database - in_model == set()


async def test_the_model_invents_no_column_the_table_lacks(
    session: AsyncSession,
) -> None:
    """And one in the model but not the SQL fails every query at runtime."""
    columns = await _nullability_by_column(session)
    in_database = set(columns)
    in_model = _MODEL_COLUMNS

    assert in_model - in_database == set()


async def test_the_model_agrees_about_what_may_be_null(
    session: AsyncSession,
) -> None:
    """Only what a writer always knows is NOT NULL. ADR 0006."""
    columns = await _nullability_by_column(session)
    nullable_in_database = {name for name, nullable in columns.items() if nullable == "YES"}
    nullable_in_model = {
        column.name for column in AdminAction.__table__.columns if column.nullable
    }

    assert nullable_in_database == nullable_in_model


@pytest.mark.parametrize(
    "column",
    [
        "occurred_at",
        "actor_id",
        "actor_username",
        "actor_role",
        "action_type",
        "target_type",
    ],
)
async def test_the_fields_the_story_requires_are_mandatory(
    session: AsyncSession, column: str
) -> None:
    """[4.3] #15's first criterion: actor, action type, target and timestamp on every entry."""
    is_nullable = await _nullability_by_column(session)

    assert is_nullable[column] == "NO"
