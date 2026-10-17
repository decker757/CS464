"""The auth schema's migrations against the models, and the migrate step. [F-5] #75

ADR 0020. Synchronous on purpose: env.py runs its own event loop
(shared/migrating.py), which cannot start inside pytest-asyncio's. Each test
empties this service's schema before and after itself, because the conftest
creates missing tables and never drops one: a schema left with a dropped column
would break every later test.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import inspect

from core.database import SCHEMA, Base
from main import create_app
from migrate import ALEMBIC_INI, main
from shared.migrating import VERSION_TABLE, run_in_transaction
from shared.testing import (
    assert_baseline_downgrades_and_upgrades_again,
    assert_migrating_an_empty_schema_builds_the_models,
    compose_service,
    drop_own_tables,
)

# The conftest has pointed DATABASE_URL at the test database by now.
_URL = os.environ["DATABASE_URL"]


def _empty_the_schema() -> None:
    run_in_transaction(_URL, lambda connection: drop_own_tables(connection, SCHEMA))


def _tables() -> list[str]:
    return run_in_transaction(
        _URL, lambda connection: inspect(connection).get_table_names(schema=SCHEMA)
    )


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
    migrated = assert_migrating_an_empty_schema_builds_the_models(
        migrate_step=main, url=_URL, metadata=Base.metadata, schema=SCHEMA
    )
    # Case-insensitive usernames, which SQLite once let through. Also proves
    # the catalog the comparison above relies on is not empty.
    assert (
        "CREATE UNIQUE INDEX uq_users_username_lower ON auth.users "
        "USING btree (lower((username)::text))"
    ) in migrated["indexes"]


def test_the_baseline_downgrades_to_nothing_and_upgrades_again(empty_schema) -> None:
    assert_baseline_downgrades_and_upgrades_again(
        migrate_step=main,
        alembic_ini=ALEMBIC_INI,
        url=_URL,
        metadata=Base.metadata,
        schema=SCHEMA,
    )


def test_a_schema_from_before_75_is_refused_and_left_as_it_was(empty_schema, capsys) -> None:
    """Tables and no migration history: the migrations cannot know what such a
    database holds, so the step changes nothing and says to start from empty.
    ADR 0020."""
    # What every boot before #75 left behind.
    run_in_transaction(_URL, Base.metadata.create_all)

    assert main() == 1

    assert "docker compose down -v" in capsys.readouterr().err
    assert VERSION_TABLE not in _tables()


def test_the_migrate_step_exits_2_without_a_database_url(monkeypatch, capsys) -> None:
    """Compose starts the service only once its migrate step exits 0, and the
    code is how a person tells a missing setting (2) from a refused or failed one (1)."""
    monkeypatch.delenv("DATABASE_URL")

    assert main() == 2

    assert "DATABASE_URL is required" in capsys.readouterr().err


def test_starting_the_app_creates_no_table(empty_schema) -> None:
    """Only the migrate step issues DDL. With create_all back in the lifespan, a
    model change would reach a database with no revision to show for it."""

    async def start_and_stop() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(start_and_stop())

    assert _tables() == []


def test_compose_starts_auth_only_after_its_migration_succeeds() -> None:
    """A failed migration stops new code before it serves a request, and the
    migration runs as the app's own role, never the superuser. ADR 0020."""
    app = compose_service("auth")
    migration = compose_service("auth-migrate")

    assert app["depends_on"]["auth-migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert migration["environment"]["DATABASE_URL"] == app["environment"]["DATABASE_URL"]
    assert migration["command"] == ["python", "migrate.py"]
    assert "://auth_svc:" in migration["environment"]["DATABASE_URL"]
    assert migration["restart"] == "no"
    assert "JWT_SECRET" not in migration["environment"]
