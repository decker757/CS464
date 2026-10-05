"""The market schema's migrations against the models, and the migrate step. [F-5] #75

ADR 0020. Synchronous on purpose: env.py runs its own event loop
(shared/migrating.py), which cannot start inside pytest-asyncio's. Each test
empties this service's schema before and after itself.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import inspect, text

from core.database import SCHEMA, Base
from main import create_app
from migrate import ALEMBIC_INI, main
from shared.migrating import VERSION_TABLE, find_drift, run_in_transaction
from shared.testing import (
    assert_baseline_downgrades_and_upgrades_again,
    compose_service,
    drop_own_tables,
    schema_catalog,
)

# The conftest has pointed DATABASE_URL at the test database by now.
_URL = os.environ["DATABASE_URL"]
_BASELINE = "0001"


def _catalog() -> dict[str, list[str]]:
    return run_in_transaction(_URL, lambda connection: schema_catalog(connection, SCHEMA))


def _empty_the_schema() -> None:
    run_in_transaction(_URL, lambda connection: drop_own_tables(connection, SCHEMA))


def _tables() -> list[str]:
    return run_in_transaction(
        _URL, lambda connection: inspect(connection).get_table_names(schema=SCHEMA)
    )


def _version() -> str:
    return run_in_transaction(
        _URL,
        lambda connection: connection.execute(
            text(f"SELECT version_num FROM {SCHEMA}.{VERSION_TABLE}")
        ).scalar_one(),
    )


def _build_like_before_75(*statements: str) -> None:
    """The schema as every boot before #75 left it, then `statements` against it."""

    def build(connection) -> None:
        Base.metadata.create_all(connection)
        for statement in statements:
            connection.execute(text(statement))

    run_in_transaction(_URL, build)


@pytest.fixture
def empty_schema():
    """This service's schema with no table in it, before and after the test."""
    _empty_the_schema()
    yield
    _empty_the_schema()


def test_migrating_an_empty_schema_builds_what_the_models_build(empty_schema) -> None:
    """The baseline, held to the models. Twice, because compose runs the step
    on every `up` and the second run must be a no-op."""
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
    # The close sweep's working set. compare_metadata never compares a partial
    # index's predicate (Alembic 1.20), so this line and the catalog above are
    # the only things that notice the baseline losing it. ADR 0011.
    assert (
        "CREATE INDEX ix_markets_due_close ON market.markets USING btree (close_time) "
        "WHERE ((status)::text = 'open'::text)"
    ) in migrated["indexes"]


def test_the_baseline_downgrades_to_nothing_and_upgrades_again(empty_schema) -> None:
    assert_baseline_downgrades_and_upgrades_again(
        migrate_step=main,
        alembic_ini=ALEMBIC_INI,
        url=_URL,
        metadata=Base.metadata,
        schema=SCHEMA,
    )


def test_a_legacy_schema_that_matches_the_models_is_stamped_and_keeps_its_rows(
    empty_schema,
) -> None:
    market_id = uuid.uuid4()
    _build_like_before_75(
        "INSERT INTO market.markets (id, creator_id, draft_key) "
        f"VALUES ('{market_id}', '{uuid.uuid4()}', '{uuid.uuid4()}')"
    )

    assert main() == 0

    kept = run_in_transaction(
        _URL,
        lambda connection: connection.execute(
            text("SELECT status FROM market.markets WHERE id = :id"), {"id": market_id}
        ).scalar_one(),
    )
    assert _version() == _BASELINE
    assert kept == "draft"


def test_a_legacy_schema_that_missed_0004_and_0006_is_refused_naming_both(
    empty_schema, capsys
) -> None:
    """The two shapes a missed hand-applied file leaves: a column (0006's
    proposal_id) and an index (0004's ix_markets_due_close)."""
    _build_like_before_75(
        "ALTER TABLE market.markets DROP COLUMN proposal_id",
        "DROP INDEX market.ix_markets_due_close",
    )

    assert main() == 1

    refusal = capsys.readouterr().err
    assert "add_column market.markets.proposal_id" in refusal
    assert "add_index market.markets: ix_markets_due_close" in refusal
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


def test_compose_starts_market_only_after_its_migration_succeeds() -> None:
    """A failed migration stops new code before it serves a request, and the
    migration runs as the app's own role, never the superuser. ADR 0020."""
    app = compose_service("market")
    migration = compose_service("market-migrate")

    assert app["depends_on"]["market-migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert migration["environment"]["DATABASE_URL"] == app["environment"]["DATABASE_URL"]
    assert migration["command"] == ["python", "migrate.py"]
    assert "://market_svc:" in migration["environment"]["DATABASE_URL"]
    assert migration["restart"] == "no"
    assert "JWT_SECRET" not in migration["environment"]
