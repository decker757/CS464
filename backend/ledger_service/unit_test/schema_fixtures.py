"""Reading a database refusal, for the tests that pin which constraint made it.

A schema test asserts the constraint's name rather than any IntegrityError,
so a refusal from some other rule does not pass for the one under test.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError


def refusing_constraint(refusal: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The constraint Postgres named when it refused the write. None for a
    not-null violation, which names a column instead."""
    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, and
    # only asyncpg's carries the constraint name.
    return refusal.value.orig.__cause__.constraint_name
