"""Keyset cursors for a user's ledger history.

Pure functions over a (timestamp, id) pair. No database, no HTTP, so the
encoding can be tested directly — which matters, because a cursor bug shows up
as rows quietly missing from a page rather than as an error.

Copied from `audit_service/core/paging.py` rather than shared, for the reason
in `core/roles.py`: a Docker build context cannot reach outside itself, so a
common package is a restructure ADR 0005 defers rather than an import. The two
logs page identically because they are the same shape of problem, and if the
encoding here ever needs to differ, it can, without breaking the other.

## Why keyset and not OFFSET

A ledger is ordered newest first and only ever grows at that end. A trader
reading page 1, then page 2, with OFFSET 50 sees any entry appended in between
push the window down by one, so the last row of page 1 reappears as the first
row of page 2. For an ordinary list that is cosmetic; for a statement of where
somebody's money went it means paging through your own history can show you a
transaction twice and hide the one behind it.

A keyset cursor names the last row seen — "continue strictly before this
(created_at, id)" — so pages stay stable no matter what is appended while the
reader works through them. It is also the query
`ix_ledger_entries_account_feed` is built for: an index seek to the cursor,
then a scan, rather than counting and discarding OFFSET rows on every page.

## Why the pair, and not the timestamp alone

`created_at` is not unique, and in this service it is emphatically not unique:
every transaction writes at least two entries carrying the identical timestamp,
because they are two halves of one movement. A cursor that cannot tell them
apart either loses one or repeats it forever. The id breaks the tie, and the
same (created_at DESC, id DESC) ordering appears in the index.

The value is opaque to clients on purpose: it encodes a position in one
ordering, and a client that constructs one by hand has invented a position this
service never promised to honour.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

from core.errors import MalformedCursor

_SEPARATOR = "|"


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    """The position just after `row_id`, as an opaque string."""
    raw = f"{created_at.isoformat()}{_SEPARATOR}{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Read a cursor this service issued.

    Raises MalformedCursor for anything else. Every failure mode collapses to
    one error deliberately: the difference between bad base64, a missing
    separator and an unparseable UUID is not something a client can act on
    differently, and describing it precisely only helps someone probing what
    the value is made of.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp, _, row_id = raw.partition(_SEPARATOR)
        if not row_id:
            raise ValueError("no separator")
        created_at = datetime.fromisoformat(timestamp)
        # A naive value would compare against an aware column and raise inside
        # the query instead of here.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at, uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise MalformedCursor from exc
