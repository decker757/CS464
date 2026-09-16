"""Keyset cursors for a user's ledger history.

The cursor format itself is in `shared/paging.py`, which this service and its
sibling held near-identical copies of before [F-6] #76. What stays here is this
service's error vocabulary: `shared.paging.decode_cursor` returns None for a
value it did not issue, and this is where that becomes a `MalformedCursor` the
controller turns into a 400.

**Why keyset and not OFFSET.** This table is ordered newest first and only ever
grows at that end. A reader taking page 1, then page 2 with OFFSET 50, sees any
row appended in between push the window down, so the last row of page 1
reappears as the first row of page 2. For an ordinary list that is cosmetic; for
ledger it means paging through the history can show a record twice and
skip the one behind it.

A cursor instead names a position — "everything strictly older than
(created_at, id)" — so pages stay stable no matter what is appended while the
reader works through them. It is also the query the indexes in
sql/02-schemas.sql are built for: an index seek to the cursor, then a scan,
rather than counting and discarding OFFSET rows on every page.

`created_at` is not unique, which is why the id is in the cursor at all. See
`shared/paging.py` for the cases that makes concrete.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor

#: One row's place in the feed: the pair the ordering, the cursor and the
#: running balance all agree on. Named because three signatures take it and
#: `tuple[datetime, uuid.UUID]` says nothing about which of the two comes
#: first.
Position = tuple[datetime, uuid.UUID]


def decode_cursor(cursor: str) -> Position:
    """Read a cursor this service issued, or raise MalformedCursor.

    Every failure mode collapses to one error deliberately: the difference
    between bad base64, a missing separator and an unparseable UUID is not
    something a client can act on differently, and describing it precisely only
    helps someone probing what the value is made of.
    """
    position = _shared.decode_cursor(cursor)
    if position is None:
        raise MalformedCursor
    return position
