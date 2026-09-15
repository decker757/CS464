"""Keyset cursors for the audit feed.

Pure functions over a (timestamp, id) pair. No database, no HTTP, so the
encoding can be tested directly — which matters, because a cursor bug shows up
as rows quietly missing from a page rather than as an error.

## Why keyset and not OFFSET

The feed is ordered newest first and the table only ever grows at that end. An
administrator reading page 1, then page 2, with OFFSET 50 sees any row appended
in between push the window down by one, so the last row of page 1 reappears as
the first row of page 2. Every row shifts by however many arrived. For an
ordinary list that is a cosmetic annoyance; for an audit log it means paging
through the history can show you a record twice and skip the one behind it.

A keyset cursor names the last row seen — "continue strictly before this
(occurred_at, id)" — so pages stay stable no matter what is appended while the
reader is working through them. It is also the query the indexes in
sql/02-schemas.sql are built for: an index seek to the cursor, then a scan,
rather than counting and discarding OFFSET rows on every page.

## Why the pair, and not the timestamp alone

`occurred_at` is not unique. Two services can append in the same microsecond,
and two rows written in one transaction usually do. A cursor that cannot tell
them apart either loses one or repeats it forever. The id breaks the tie, and
the same (occurred_at DESC, id DESC) ordering appears in every index.

The value is opaque to clients on purpose: it encodes a position in one
ordering, and a client that constructs one by hand has invented a position
this service never promised to honour.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

from core.errors import MalformedCursor

_SEPARATOR = "|"


def encode_cursor(occurred_at: datetime, row_id: uuid.UUID) -> str:
    """The position just after `row_id`, as an opaque string."""
    raw = f"{occurred_at.isoformat()}{_SEPARATOR}{row_id}"
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
        occurred_at = datetime.fromisoformat(timestamp)
        # A naive value would compare against an aware column and raise inside
        # the query instead of here.
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return occurred_at, uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise MalformedCursor from exc
