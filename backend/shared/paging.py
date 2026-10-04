"""The keyset cursor format. [F-6] #76

Shared between the audit feed and the ledger history, which page identically
because they are the same shape of problem: an append-only table read newest
first, where OFFSET would let rows arriving mid-read shift the window and show
a reader the same row twice while hiding the one behind it.

A cursor is `(timestamp, id)` base64'd. The id is not decoration — neither
timestamp is unique. Two services can append in the same microsecond, and in
the ledger every transaction writes at least two entries carrying the identical
timestamp by construction, because they are two halves of one movement. A
cursor that cannot tell them apart either loses one or repeats it forever.

**What this module deliberately does not own: the error.** `decode_cursor`
returns None rather than raising, and each service's `core/paging.py` turns
that into its own `MalformedCursor`. The alternative would be a shared
exception base class, which is the "sharing `core`" that ADR 0005 ruled out —
and the point of each service having its own error hierarchy is that a ledger
error cannot be caught by an `except` in the audit service.

The format is opaque on purpose. A client that constructs one by hand has
invented a position no service ever promised to honour.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

_SEPARATOR = "|"


def encode_cursor(timestamp: datetime, row_id: uuid.UUID) -> str:
    """The position just after `row_id`, as an opaque string."""
    raw = f"{timestamp.isoformat()}{_SEPARATOR}{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    """Read a cursor `encode_cursor` issued, or None if it is not one.

    Every failure mode collapses to None deliberately: the difference between
    bad base64, a missing separator and an unparseable UUID is not something a
    client can act on differently, and describing it precisely only helps
    someone probing what the value is made of.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp, _, row_id = raw.partition(_SEPARATOR)
        if not row_id:
            raise ValueError("no separator")
        moment = datetime.fromisoformat(timestamp)
        # A naive value would compare against an aware column and raise inside
        # the query instead of here.
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
