"""Normalising a datetime before it is compared.

In `core/` so both of its callers, `service/validation.py` and
`core/closing.py`, can import it.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime, assuming UTC if it has no offset.

    It should never have to assume: Postgres and the schemas both produce
    aware values. It exists because comparing naive with aware raises
    `TypeError`, which would be a 500 on submit or a dead sweep.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
