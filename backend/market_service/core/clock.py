"""Normalising a datetime before it is compared.

One function, extracted because two service modules now need it and because
the mistake it prevents has already cost this repository time: comparing a
naive datetime to an aware one raises `TypeError`, and this service is almost
entirely about timestamps.

In `core/` rather than beside either caller, so that neither
`service/validation.py` nor `service/closing.py` has to import the other for a
datetime helper that is about neither validation nor closing. It touches no
entity and no session, which is what makes that placement legal under the
import rule in CLAUDE.md.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime, assuming UTC if it has no offset.

    This should never actually have to assume. Postgres hands back aware
    datetimes on every `timestamptz` column, and `model/schemas.py` types every
    inbound timestamp as `AwareDatetime`, so a naive value reaching here means
    something constructed an entity by hand.

    It is here anyway because the alternative is a `TypeError` surfacing as a
    500 on the submit button, or on the sweep quietly dying, which is a far
    worse failure than an assumption written down and documented.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
