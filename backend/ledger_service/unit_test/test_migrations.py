"""The ledger schema's migrations against the models, and the migrate step. [F-5] #75

ADR 0020. Synchronous on purpose: env.py runs its own event loop
(shared/migrating.py), which cannot start inside pytest-asyncio's. Each test
empties this service's schema before and after itself.

compare_metadata cannot see a trigger, so the append-only one is checked here
two ways: by its definition and by what it refuses. ADR 0009.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from core.database import SCHEMA, Base
from main import create_app
from migrate import ALEMBIC_INI, main
from shared.migrating import VERSION_TABLE, alembic_config, run_in_transaction
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
    """The baseline, held to the models, the trigger included: the shared
    helper compares every trigger definition with the create_all build's."""
    migrated = assert_migrating_an_empty_schema_builds_the_models(
        migrate_step=main, url=_URL, metadata=Base.metadata, schema=SCHEMA
    )
    # By name as well, so the trigger leaving the models and the baseline
    # together still goes red. ADR 0009.
    assert (
        "CREATE TRIGGER entries_append_only BEFORE DELETE OR UPDATE OR TRUNCATE "
        "ON ledger.entries FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()"
    ) in migrated["triggers"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger.entries SET amount = 1",
        "DELETE FROM ledger.entries",
        "TRUNCATE ledger.entries",
    ],
)
def test_a_migrated_ledger_refuses_to_change_an_entry(empty_schema, statement: str) -> None:
    """ADR 0009's append-only rule, on a database the migrate step built rather
    than the suite. Statement-level, so an empty table is enough."""
    assert main() == 0

    with pytest.raises(DBAPIError, match="append-only"):
        run_in_transaction(_URL, lambda connection: connection.execute(text(statement)))


def test_the_baseline_downgrades_to_nothing_and_upgrades_again(empty_schema) -> None:
    assert_baseline_downgrades_and_upgrades_again(
        migrate_step=main,
        alembic_ini=ALEMBIC_INI,
        url=_URL,
        metadata=Base.metadata,
        schema=SCHEMA,
    )


def test_the_downgrade_leaves_no_trigger_function_behind(empty_schema) -> None:
    """The one object the baseline makes that is not a table, so dropping the
    tables does not take it with them."""
    assert main() == 0

    command.downgrade(alembic_config(ALEMBIC_INI, _URL), "base")

    left = run_in_transaction(
        _URL,
        lambda connection: connection.execute(
            text(f"SELECT to_regprocedure('{SCHEMA}.reject_mutation()')")
        ).scalar_one(),
    )
    assert left is None


def test_a_schema_from_before_75_is_refused_and_left_as_it_was(empty_schema, capsys) -> None:
    """Tables and no migration history: the migrations cannot know what such a
    database holds, so the step changes nothing and says to start from empty.
    ADR 0020."""
    # What every boot before #75 left behind.
    run_in_transaction(_URL, Base.metadata.create_all)

    assert main() == 1

    assert "docker compose down -v" in capsys.readouterr().err
    assert VERSION_TABLE not in _tables()


def test_starting_the_app_creates_no_table(empty_schema) -> None:
    """Only the migrate step issues DDL. With create_all back in the lifespan, a
    model change would reach a database with no revision to show for it."""

    async def start_and_stop() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(start_and_stop())

    assert _tables() == []


def test_compose_starts_the_ledger_only_after_its_migration_succeeds() -> None:
    """A failed migration stops new code before it serves a request, and the
    migration runs as the app's own role, never the superuser. ADR 0020."""
    app = compose_service("ledger")
    migration = compose_service("ledger-migrate")
    environment = migration["environment"]

    assert app["depends_on"]["ledger-migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert environment["DATABASE_URL"] == app["environment"]["DATABASE_URL"]
    assert migration["command"] == ["python", "migrate.py"]
    assert "://ledger_svc:" in environment["DATABASE_URL"]
    assert migration["restart"] == "no"
    assert "JWT_SECRET" not in environment
    assert "REDIS_URL" not in environment
    assert "MARKET_SERVICE_URL" not in environment
