"""The migrate step: bring the ledger schema to head, or refuse. [F-5] #75, ADR 0020.

Compose runs it as `ledger-migrate` and starts `ledger` only if it exits 0; CI runs
it before the suite. `python migrate.py`, with DATABASE_URL naming this
service's own role. A database from before #75, with tables and no migration
history, is refused and left as it was.
"""

from __future__ import annotations

import logging
import pathlib
import sys

from core.database import SCHEMA
from shared.migrating import migrate_from_environment

ALEMBIC_INI = pathlib.Path(__file__).resolve().parent / "alembic.ini"


def main() -> int:
    """Migrate the database DATABASE_URL names. Returns the process exit code."""
    return migrate_from_environment(alembic_ini=ALEMBIC_INI, schema=SCHEMA)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
