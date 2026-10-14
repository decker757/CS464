"""Normalising a datetime to UTC.

In `core/` so the response models in `model/schemas.py` can import it.
"""

from __future__ import annotations

from datetime import UTC, datetime


# Twin of `backend/market_service/core/clock.py::as_utc`; copied, not shared.
def as_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime, assuming UTC if it has no offset.

    It should never have to assume: Postgres and the callers both produce
    aware values. It exists so a naive datetime never reaches the wire with
    no offset, which every browser reads as local time.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
