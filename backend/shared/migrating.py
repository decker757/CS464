"""Running Alembic the same way in every service that owns a schema. [F-5] #75

ADR 0020. auth, market and ledger bind this to their own metadata and schema in
`migrations/env.py` and `migrate.py`, and nothing here imports a service. One
copy, because a divergence would be a bug rather than a design choice: a schema
filter that let `audit` in would offer to drop a table no service may touch,
and two adoption rules would adopt a database the third refuses. ADR 0012's bar.

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
triggers. Each service's guard tests compare those through the catalog, and
`extra_drift` lets a service add a check of its own (the ledger's append-only
trigger) to the adoption of a database from before #75.

Never imported by the realtime or audit services, which have no Alembic.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from alembic import command, context
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

VERSION_TABLE = "alembic_version"

# Where `_alembic_config` hands the URL to env.py. An attribute, not
# `sqlalchemy.url`: configparser would %-interpolate a URL-encoded password.
_URL_ATTRIBUTE = "database_url"

_REFUSED_FOR_DRIFT = (
    "{schema}: this database has tables but no migration history, and they do "
    "not match the models:\n{differences}\n\n"
    "It was built before #75 and missed a hand-applied change. Apply the "
    "missing file from sql/migrations/ as it stood before #75 (git log -- "
    "sql/migrations/ finds it) and run this again, or start from an empty "
    "database with `docker compose down -v`, which destroys local data. "
    "Nothing was changed."
)

_REFUSED_PAST_BASELINE = (
    "{schema}: this database has tables but no migration history, and the "
    "migrations have moved past the baseline it could have been matched "
    "against. Start from an empty database with `docker compose down -v`, "
    "which destroys local data. Nothing was changed."
)

Result = TypeVar("Result")
DriftCheck = Callable[[Connection], list[str]]

logger = logging.getLogger(__name__)


class LegacyDrift(Exception):
    """A database from before #75 that the migrate step will not adopt. The message says why."""


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


def _describe_change(change: tuple[Any, ...]) -> str:
    kind = change[0]
    if kind in ("add_table", "remove_table"):
        return f"{kind} {change[1].fullname}"
    if kind in ("add_column", "remove_column"):
        _, schema, table, column = change
        return f"{kind} {schema}.{table}.{column.name}"
    if kind.startswith("modify_"):
        _, schema, table, column_name = change[:4]
        return f"{kind} {schema}.{table}.{column_name}"
    # An index, a unique constraint or a foreign key: each knows its table.
    item = change[1]
    return f"{kind} {item.table.fullname}: {item.name or '(unnamed)'}"


def describe_drift(differences: list[Any]) -> list[str]:
    """One line per difference compare_metadata found, naming what differs."""
    lines: list[str] = []
    for difference in differences:
        # Changes to one column arrive grouped in a list.
        if isinstance(difference, list):
            lines.extend(_describe_change(change) for change in difference)
        else:
            lines.append(_describe_change(difference))
    return lines


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


def _context_options(metadata: MetaData, schema: str) -> dict[str, Any]:
    return {
        "target_metadata": metadata,
        "version_table_schema": schema,
        "include_schemas": True,
        "include_name": functools.partial(is_own_name, schema=schema),
        "compare_type": True,
        "compare_server_default": True,
    }


def find_drift(
    connection: Connection,
    *,
    metadata: MetaData,
    schema: str,
    extra_drift: DriftCheck | None = None,
) -> list[str]:
    """Every way `schema` differs from `metadata`, one line each; empty when they match.

    compare_metadata, plus whatever `extra_drift` finds that it cannot see.
    """
    migration_context = MigrationContext.configure(
        connection, opts=_context_options(metadata, schema)
    )
    lines = describe_drift(compare_metadata(migration_context, metadata))
    if extra_drift is not None:
        lines.extend(extra_drift(connection))
    return lines


def _run_migrations(connection: Connection, *, metadata: MetaData, schema: str) -> None:
    context.configure(connection=connection, **_context_options(metadata, schema))
    with context.begin_transaction():
        context.run_migrations()


def run_env(*, metadata: MetaData, schema: str) -> None:
    """The body of a service's `migrations/env.py`: run Alembic's command online.

    The URL comes from `_alembic_config`, or from DATABASE_URL for the `alembic`
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


def _alembic_config(alembic_ini: Path, url: str) -> Config:
    config = Config(str(alembic_ini))
    config.attributes[_URL_ATTRIBUTE] = url
    return config


def _own_tables(connection: Connection, *, schema: str) -> set[str]:
    return set(inspect(connection).get_table_names(schema=schema))


def _adopt_legacy_schema(
    connection: Connection,
    *,
    metadata: MetaData,
    schema: str,
    extra_drift: DriftCheck | None,
) -> None:
    # The create_all every boot ran before #75, so a database that was only
    # behind on tables is not drift. Inside the caller's transaction, which a
    # refusal rolls back, so a refused database keeps none of them. ADR 0020.
    metadata.create_all(connection)
    differences = find_drift(
        connection, metadata=metadata, schema=schema, extra_drift=extra_drift
    )
    if differences:
        listed = "\n".join(f"  - {line}" for line in differences)
        raise LegacyDrift(_REFUSED_FOR_DRIFT.format(schema=schema, differences=listed))


def migrate(
    *,
    alembic_ini: Path,
    url: str,
    metadata: MetaData,
    schema: str,
    extra_drift: DriftCheck | None = None,
) -> None:
    """Bring `schema` to head, adopting a database from before #75 first.

    With a version table: upgrade. With none of the service's tables: upgrade
    from nothing. With tables and no version table, the database predates #75:
    it gets any missing table, is compared with `metadata`, and is stamped at
    the baseline if they match. Raises LegacyDrift, having changed nothing, if
    they do not or if the migrations have moved past the baseline. ADR 0020.
    """
    config = _alembic_config(alembic_ini, url)
    tables = run_in_transaction(url, functools.partial(_own_tables, schema=schema))
    if tables and VERSION_TABLE not in tables:
        scripts = ScriptDirectory.from_config(config)
        baseline = scripts.get_base()
        # Matching the models proves a database is at head, and head is the
        # baseline only until a second revision exists.
        if scripts.get_current_head() != baseline:
            raise LegacyDrift(_REFUSED_PAST_BASELINE.format(schema=schema))
        run_in_transaction(
            url,
            functools.partial(
                _adopt_legacy_schema,
                metadata=metadata,
                schema=schema,
                extra_drift=extra_drift,
            ),
        )
        command.stamp(config, baseline)
        logger.info("%s: adopted a database from before #75 at revision %s", schema, baseline)
    command.upgrade(config, "head")


def migrate_from_environment(
    *,
    alembic_ini: Path,
    metadata: MetaData,
    schema: str,
    extra_drift: DriftCheck | None = None,
) -> int:
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
        migrate(
            alembic_ini=alembic_ini,
            url=url,
            metadata=metadata,
            schema=schema,
            extra_drift=extra_drift,
        )
    except LegacyDrift as refusal:
        print(refusal, file=sys.stderr)
        return 1
    return 0
