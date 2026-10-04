"""The auth schema's migrations against the models, and the migrate step. [F-5] #75

ADR 0020. Synchronous on purpose: env.py runs its own event loop
(shared/migrating.py), which cannot start inside pytest-asyncio's. Each test
empties this service's schema before and after itself, because the conftest
creates missing tables and never drops one: a schema left with a dropped column
would break every later test.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import uuid

import pytest
from sqlalchemy import inspect, text

from core.database import SCHEMA, Base
from migrate import ALEMBIC_INI, main
from shared.migrating import (
    VERSION_TABLE,
    LegacyDrift,
    find_drift,
    migrate,
    run_in_transaction,
)
from shared.testing import drop_own_tables, schema_catalog

# The conftest has pointed DATABASE_URL at the test database by now.
_URL = os.environ["DATABASE_URL"]
_SERVICE_DIR = pathlib.Path(__file__).resolve().parents[1]
_BASELINE = "0001"

_SECOND_REVISION = '''\
"""A revision after the baseline, for one test only."""

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _catalog() -> dict[str, list[str]]:
    return run_in_transaction(_URL, lambda connection: schema_catalog(connection, SCHEMA))


def _empty_the_schema() -> None:
    run_in_transaction(_URL, lambda connection: drop_own_tables(connection, SCHEMA))


def _tables() -> list[str]:
    return run_in_transaction(
        _URL, lambda connection: inspect(connection).get_table_names(schema=SCHEMA)
    )


def _has_version_table() -> bool:
    return VERSION_TABLE in _tables()


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


def test_a_legacy_schema_that_matches_the_models_is_stamped_and_keeps_its_rows(
    empty_schema,
) -> None:
    user_id = uuid.uuid4()
    _build_like_before_75(
        "INSERT INTO auth.users (id, username, email, password_hash, is_suspended) "
        f"VALUES ('{user_id}', 'legacy_user', 'legacy@example.com', 'not-a-real-hash', false)"
    )

    assert main() == 0

    kept = run_in_transaction(
        _URL,
        lambda connection: connection.execute(
            text("SELECT username FROM auth.users WHERE id = :id"), {"id": user_id}
        ).scalar_one(),
    )
    assert _version() == _BASELINE
    assert kept == "legacy_user"


def test_a_legacy_schema_missing_a_whole_table_gets_it_and_is_stamped(empty_schema) -> None:
    """Every boot before #75 ran create_all, so a database that is only behind
    on tables is what the old code would have fixed by starting. ADR 0020."""
    _build_like_before_75("DROP TABLE auth.refresh_tokens")

    assert main() == 0

    assert _version() == _BASELINE
    assert "refresh_tokens" in _tables()


def test_a_drifted_legacy_schema_is_refused_and_left_as_it_was(empty_schema, capsys) -> None:
    """A database that missed a hand-applied ALTER: refused, naming the column,
    with nothing stamped and the table the check created rolled back."""
    _build_like_before_75(
        "ALTER TABLE auth.users DROP COLUMN is_suspended",
        "DROP TABLE auth.refresh_tokens",
    )

    assert main() == 1

    assert "add_column auth.users.is_suspended" in capsys.readouterr().err
    assert not _has_version_table()
    assert "refresh_tokens" not in _tables()


def test_a_legacy_schema_is_refused_once_the_migrations_pass_the_baseline(
    empty_schema, tmp_path
) -> None:
    """Matching the models proves head, and head is the baseline only until a
    second revision exists; a stamp at the baseline would then claim changes
    the database never had. ADR 0020."""
    shutil.copytree(
        _SERVICE_DIR / "migrations",
        tmp_path / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy(ALEMBIC_INI, tmp_path / "alembic.ini")
    (tmp_path / "migrations" / "versions" / "0002_later.py").write_text(_SECOND_REVISION)
    _build_like_before_75()

    with pytest.raises(LegacyDrift, match="moved past the baseline"):
        migrate(
            alembic_ini=tmp_path / "alembic.ini",
            url=_URL,
            metadata=Base.metadata,
            schema=SCHEMA,
        )

    assert not _has_version_table()


def test_the_migrate_step_exits_2_without_a_database_url(monkeypatch, capsys) -> None:
    """Compose starts the service only once its migrate step exits 0, and the
    code is how a person tells a missing setting (2) from a refused database (1)."""
    monkeypatch.delenv("DATABASE_URL")

    assert main() == 2

    assert "DATABASE_URL is required" in capsys.readouterr().err
