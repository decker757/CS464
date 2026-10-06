"""Running Alembic the same way in every service that owns a schema. [F-5] #75

ADR 0020. auth, market and ledger bind this to their own metadata and schema in
`migrations/env.py` and `migrate.py`, and nothing here imports a service. One
copy, because a divergence would be a bug rather than a design choice: a schema
filter that let `audit` in would offer to drop a table no service may touch.
ADR 0012's bar.

Two things here are load-bearing and not obvious.

Every connection sets `search_path` to `public`, never the service's schema.
sql/02-schemas.sql gives each role `search_path = <its own schema>`, and Alembic
reports the connection's default schema as None. `is_own_name` reads None as
`public` and leaves it out, so without the override the service's whole schema
is invisible and every model table reads as missing (add_table, add_index).
Mapping None to the service's schema instead would reflect its foreign keys
without a schema while the models name one, and every foreign key would come
back dropped and re-added (checked against Alembic 1.20.0).

compare_metadata cannot see CHECK constraints, partial-index predicates or
triggers. Each service's guard tests compare those through the catalog.

Never imported by the realtime or audit services, which have no Alembic.
"""

from __future__ import annotations

import asyncio
import functools
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from alembic import command, context
from alembic.config import Config
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

VERSION_TABLE = "alembic_version"

# Where `alembic_config` hands the URL to env.py. An attribute, not
# `sqlalchemy.url`: configparser would %-interpolate a URL-encoded password.
_URL_ATTRIBUTE = "database_url"

_REFUSED_WITHOUT_HISTORY = (
    "{schema}: this database has tables but no migration history, so something "
    "other than the migrations built it, most likely a boot from before #75. "
    "The migrate step cannot know what it holds (ADR 0020). Start from an "
    "empty database with `docker compose down -v`, which destroys local data. "
    "Nothing was changed."
)

Result = TypeVar("Result")


class NoMigrationHistory(Exception):
    """Tables and no version table, which the migrate step refuses. The message says what to do."""


def is_own_name(
    name: str | None, type_: str, parent_names: dict[str, Any], *, schema: str
) -> bool:
    """Alembic's `include_name`: True for `schema` and everything inside it.

    Every other schema is left out, `audit` above all: its one table belongs to
    the superuser. The connection's default schema arrives as None and is
    `public` here, so it is left out too.
    """
    if type_ == "schema":
        return name == schema
    return True


def _migration_engine(url: str) -> AsyncEngine:
    # search_path=public: see the module docstring. NullPool: every use is one
    # short piece of work, and a pool would outlive its event loop.
    return create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": "public"}},
    )


async def _run_async(url: str, work: Callable[[Connection], Result]) -> Result:
    engine = _migration_engine(url)
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(work)
    finally:
        await engine.dispose()


def run_in_transaction(url: str, work: Callable[[Connection], Result]) -> Result:
    """Run `work` on one connection in one transaction, committed if it returns.

    Rolled back if it raises. Starts its own event loop, so it is for
    synchronous callers only. The search_path is `public`, so `work` names
    every table with its schema.
    """
    return asyncio.run(_run_async(url, work))


def context_options(metadata: MetaData, schema: str) -> dict[str, Any]:
    """Alembic's options for `schema`: only its own names, types and defaults compared.

    The guard tests compare with these too (shared/testing.py), so a test sees
    exactly what a migration run sees.
    """
    return {
        "target_metadata": metadata,
        "version_table_schema": schema,
        "include_schemas": True,
        "include_name": functools.partial(is_own_name, schema=schema),
        "compare_type": True,
        "compare_server_default": True,
    }


def _run_migrations(connection: Connection, *, metadata: MetaData, schema: str) -> None:
    context.configure(connection=connection, **context_options(metadata, schema))
    with context.begin_transaction():
        context.run_migrations()


def run_env(*, metadata: MetaData, schema: str) -> None:
    """The body of a service's `migrations/env.py`: run Alembic's command online.

    The URL comes from `alembic_config`, or from DATABASE_URL for the `alembic`
    command line. Raises RuntimeError in offline (`--sql`) mode or with no URL.
    Leaves logging alone: Alembic's usual `fileConfig` disables every existing
    logger, and the suites' `caplog` reads several.
    """
    if context.is_offline_mode():
        raise RuntimeError(
            "Offline (--sql) migrations are not supported; run against a database."
        )
    url = context.config.attributes.get(_URL_ATTRIBUTE) or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required and has no default.")
    run_in_transaction(
        url, functools.partial(_run_migrations, metadata=metadata, schema=schema)
    )


def alembic_config(alembic_ini: Path, url: str) -> Config:
    """Alembic's config for `alembic_ini`, carrying `url` to `run_env`.

    For `alembic.command` calls; the guard tests use it to downgrade.
    """
    config = Config(str(alembic_ini))
    config.attributes[_URL_ATTRIBUTE] = url
    return config


def _own_tables(connection: Connection, *, schema: str) -> set[str]:
    return set(inspect(connection).get_table_names(schema=schema))


def migrate(*, alembic_ini: Path, url: str, schema: str) -> None:
    """Bring `schema` to head, or refuse a database from before #75.

    With a version table: upgrade. With none of the service's tables: upgrade
    from nothing. With tables and no version table, the database was built
    before #75 and the migrations cannot know what it holds: raises
    NoMigrationHistory, having changed nothing. ADR 0020.
    """
    tables = run_in_transaction(url, functools.partial(_own_tables, schema=schema))
    if tables and VERSION_TABLE not in tables:
        raise NoMigrationHistory(_REFUSED_WITHOUT_HISTORY.format(schema=schema))
    command.upgrade(alembic_config(alembic_ini, url), "head")


def migrate_from_environment(*, alembic_ini: Path, schema: str) -> int:
    """`migrate` the database DATABASE_URL names, as an exit code: 0 done, 1 refused or failed, 2 no URL.

    A refusal is printed to stderr; any other error exits 1 with its
    traceback. Reads DATABASE_URL alone rather than the service's settings, so
    the migrate step needs no signing key.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required and has no default.", file=sys.stderr)
        return 2
    try:
        migrate(alembic_ini=alembic_ini, url=url, schema=schema)
    except NoMigrationHistory as refusal:
        print(refusal, file=sys.stderr)
        return 1
    return 0
