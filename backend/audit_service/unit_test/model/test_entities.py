"""The read model against the table it describes. [4.3] #15

`model/entities.py` hand-describes a table sql/02-schemas.sql owns, and only
this file stops the two drifting; ADR 0020 left that table in sql/.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UnaryExpression, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import operators

from model.entities import AdminAction

_COLUMNS = text(
    "SELECT column_name, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'audit' AND table_name = 'admin_actions'"
)
_MODEL_COLUMNS = frozenset(column.name for column in AdminAction.__table__.columns)

_DEFAULTED_COLUMNS = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = 'audit' AND table_name = 'admin_actions' "
    "AND column_default IS NOT NULL"
)

# Each index's columns in order, and whether each is descending: bit 1 of
# `indoption` is DESC. The primary key is left out; the model's
# `primary_key=True` already states it.
_INDEX_COLUMNS = text(
    "SELECT index_class.relname AS index_name, attribute.attname AS column_name, "
    "(ix.indoption[key.position - 1] & 1) = 1 AS descending "
    "FROM pg_index ix "
    "JOIN pg_class table_class ON table_class.oid = ix.indrelid "
    "JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace "
    "JOIN pg_class index_class ON index_class.oid = ix.indexrelid "
    "CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS key(attnum, position) "
    "JOIN pg_attribute attribute "
    "ON attribute.attrelid = table_class.oid AND attribute.attnum = key.attnum "
    "WHERE namespace.nspname = 'audit' AND table_class.relname = 'admin_actions' "
    "AND NOT ix.indisprimary "
    "ORDER BY index_class.relname, key.position"
)


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


async def test_the_model_declares_the_defaults_the_table_has(
    session: AsyncSession,
) -> None:
    """occurred_at's DEFAULT now() is the SQL's safety net for a row inserted by hand. #674"""
    rows = (await session.execute(_DEFAULTED_COLUMNS)).scalars()
    defaulted_in_database = set(rows)
    defaulted_in_model = {
        column.name
        for column in AdminAction.__table__.columns
        if column.server_default is not None
    }

    assert defaulted_in_database == defaulted_in_model


def _index_columns_in_model() -> dict[str, list[tuple[str, bool]]]:
    """Each declared index's columns in order, each paired with whether it descends."""
    indexes: dict[str, list[tuple[str, bool]]] = {}
    for index in AdminAction.__table__.indexes:
        columns = []
        for expression in index.expressions:
            descending = (
                isinstance(expression, UnaryExpression)
                and expression.modifier is operators.desc_op
            )
            column = expression.element if descending else expression
            columns.append((column.name, descending))
        indexes[index.name] = columns
    return indexes


async def test_the_model_declares_the_indexes_the_table_has(
    session: AsyncSession,
) -> None:
    """Same names, same columns, same direction: the reads page newest first. #674"""
    rows = (await session.execute(_INDEX_COLUMNS)).mappings()
    in_database: dict[str, list[tuple[str, bool]]] = {}
    for row in rows:
        in_database.setdefault(row.index_name, []).append((row.column_name, row.descending))

    assert in_database == _index_columns_in_model()


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
