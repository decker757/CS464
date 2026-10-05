"""The migrate step: bring the ledger schema to head, or refuse. [F-5] #75, ADR 0020.

Compose runs it as `ledger-migrate` and starts `ledger` only if it exits 0; CI
runs it before the suite. `python migrate.py`, with DATABASE_URL naming this
service's own role. A database from before #75 that does not match the models,
or that has lost its append-only trigger, is refused with what differs and left
as it was.
"""

from __future__ import annotations

import logging
import pathlib
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

from core.database import SCHEMA, Base
from model.entities import APPEND_ONLY_TRIGGER
from shared.migrating import migrate_from_environment

ALEMBIC_INI = pathlib.Path(__file__).resolve().parent / "alembic.ini"

_FIND_TRIGGER = text(
    "SELECT 1 FROM pg_trigger t "
    "JOIN pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relname = 'entries' AND t.tgname = :trigger"
)


def _missing_append_only_trigger(connection: Connection) -> list[str]:
    # compare_metadata cannot see a trigger, and a ledger without this one
    # accepts UPDATE and DELETE on money. ADR 0009.
    found = connection.execute(
        _FIND_TRIGGER, {"schema": SCHEMA, "trigger": APPEND_ONLY_TRIGGER}
    ).first()
    if found is not None:
        return []
    return [f"missing trigger {SCHEMA}.entries: {APPEND_ONLY_TRIGGER}"]


def main() -> int:
    """Migrate the database DATABASE_URL names. Returns the process exit code."""
    return migrate_from_environment(
        alembic_ini=ALEMBIC_INI,
        metadata=Base.metadata,
        schema=SCHEMA,
        extra_drift=_missing_append_only_trigger,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
