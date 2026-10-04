"""The auth schema's migrations against the models, and the migrate step. [F-5] #75

ADR 0020. Synchronous on purpose: env.py runs its own event loop
(shared/migrating.py), which cannot start inside pytest-asyncio's. Each test
empties this service's schema before and after itself, because the conftest
creates missing tables and never drops one: a schema left with a dropped column
would break every later test.
"""

from __future__ import annotations

import os

import pytest

from core.database import SCHEMA, Base
from migrate import main
from shared.migrating import find_drift, run_in_transaction
from shared.testing import drop_own_tables, schema_catalog

# The conftest has pointed DATABASE_URL at the test database by now.
_URL = os.environ["DATABASE_URL"]


def _catalog() -> dict[str, list[str]]:
    return run_in_transaction(_URL, lambda connection: schema_catalog(connection, SCHEMA))


def _empty_the_schema() -> None:
    run_in_transaction(_URL, lambda connection: drop_own_tables(connection, SCHEMA))


@pytest.fixture
def empty_schema():
    """This service's schema with no table in it, before and after the test."""
    _empty_the_schema()
    yield
    _empty_the_schema()


def test_migrating_an_empty_schema_builds_what_the_models_build(empty_schema) -> None:
    """The baseline, held to the models.

    Twice, because compose runs the step on every `up` and the second run must
    be a no-op.
    """
    assert main() == 0
    assert main() == 0
    migrated = _catalog()
    drift = run_in_transaction(
        _URL,
        lambda connection: find_drift(connection, metadata=Base.metadata, schema=SCHEMA),
    )

    _empty_the_schema()
    run_in_transaction(_URL, Base.metadata.create_all)

    assert drift == []
    assert migrated == _catalog()
    # Case-insensitive usernames, which SQLite once let through. Also proves
    # the catalog the comparison above relies on is not empty.
    assert (
        "CREATE UNIQUE INDEX uq_users_username_lower ON auth.users "
        "USING btree (lower((username)::text))"
    ) in migrated["indexes"]
