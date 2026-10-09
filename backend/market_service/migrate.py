"""The migrate step: bring the market schema to head, or refuse. [F-5] #75, ADR 0020.

Compose runs it as `market-migrate` and starts `market` only if it exits 0; CI runs
it before the suite. `python migrate.py`, with DATABASE_URL naming this
service's own role. A database from before #75 that does not match the models
is refused with what differs, and left as it was.
"""

from __future__ import annotations

import logging
import pathlib
import sys

from core.database import SCHEMA, Base
from model import entities  # noqa: F401  - registers every table on Base
from shared.migrating import migrate_from_environment

ALEMBIC_INI = pathlib.Path(__file__).resolve().parent / "alembic.ini"


def main() -> int:
    """Migrate the database DATABASE_URL names. Returns the process exit code."""
    return migrate_from_environment(
        alembic_ini=ALEMBIC_INI, metadata=Base.metadata, schema=SCHEMA
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
