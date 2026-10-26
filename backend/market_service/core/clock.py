"""Normalising a datetime before it is compared, and checking it can be sent.

In `core/` so every caller can import it: `service/validation.py` and
`core/closing.py` call `as_utc`, and `controller/public_routes.py` and
`model/schemas.py` call `has_utc_instant`.
"""

from __future__ import annotations

from datetime import UTC, datetime


# Twin of `backend/ledger_service/core/clock.py::as_utc`; copied, not shared.
def as_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime, assuming UTC if it has no offset.

    It should never have to assume: Postgres and the schemas both produce
    aware values. It exists because comparing naive with aware raises
    `TypeError`, which would be a 500 on submit or a dead sweep.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def has_utc_instant(moment: datetime) -> bool:
    """Does `moment` name an instant inside year 1 to 9999 in UTC? #490.

    asyncpg converts every bound to UTC before it sends it, and a time near
    either end of `datetime`'s range can fall outside it there: the last hour
    of year 9999 at UTC-5, or year 1 at UTC+1. Only that is refused; every
    instant a `datetime` can hold in UTC still passes.
    """
    try:
        moment.astimezone(UTC)
    except OverflowError:
        return False
    return True
