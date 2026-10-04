"""The parts of the migration runner that need no database. [F-5] #75, ADR 0020.

Adopting, refusing and upgrading need Postgres, so they are tested in each
service's `unit_test/test_migrations.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects.postgresql import VARCHAR

from shared.migrating import describe_drift, is_own_name


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


def test_a_difference_is_described_by_the_full_name_of_what_differs() -> None:
    """The refusal has to tell somebody which hand-applied file they missed."""
    differences = [
        ("add_table", Table("positions", MetaData(schema="ledger"))),
        ("add_column", "auth", "users", Column("is_suspended", String)),
        # Changes to one column arrive grouped in a list.
        [
            (
                "modify_type",
                "ledger",
                "transactions",
                "idempotency_key",
                {},
                VARCHAR(120),
                String(255),
            )
        ],
    ]

    assert describe_drift(differences) == [
        "add_table ledger.positions",
        "add_column auth.users.is_suspended",
        "modify_type ledger.transactions.idempotency_key",
    ]
