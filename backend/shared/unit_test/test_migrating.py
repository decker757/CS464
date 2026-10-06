"""The parts of the migration runner that need no database. [F-5] #75, ADR 0020.

Refusing and upgrading need Postgres, so they are tested in each
service's `unit_test/test_migrations.py`.
"""

from __future__ import annotations

import pytest

from shared.migrating import is_own_name


@pytest.mark.parametrize(
    ("name", "type_", "expected"),
    [
        ("auth", "schema", True),
        # The shared log belongs to the superuser; no service's migrations may
        # offer to create or drop it.
        ("audit", "schema", False),
        ("users", "table", True),
    ],
)
def test_only_the_services_own_schema_is_compared(
    name: str, type_: str, expected: bool
) -> None:
    assert is_own_name(name, type_, {}, schema="auth") is expected

