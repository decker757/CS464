"""Alembic's entry into the ledger schema. [F-5] #75, ADR 0020.

A composition root, like main.py and migrate.py: it binds this service's
metadata and schema to the shared runner and does nothing else.
"""

from __future__ import annotations

from core.database import SCHEMA, Base
from model import entities  # noqa: F401  - registers every table on Base
from shared.migrating import run_env

run_env(metadata=Base.metadata, schema=SCHEMA)
