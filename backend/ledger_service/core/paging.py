"""Keyset cursors for a user's ledger history.

The format is `shared/paging.py`'s; this turns its None into `MalformedCursor`.
Keyset, not OFFSET: the history grows at the newest end, so OFFSET pages would
repeat a row and skip another as entries arrive. The id is in the cursor
because `created_at` is not unique.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor

#: One row's place in the feed: the (created_at, id) pair the ordering, the
#: cursor and the running balance all agree on.
Position = tuple[datetime, uuid.UUID]


def decode_cursor(cursor: str) -> Position:
    """Read a cursor this service issued, or raise MalformedCursor.

    Every failure is one error: the client cannot act on the difference, and
    detail only helps someone probing the format.
    """
    position = _shared.decode_cursor(cursor)
    if position is None:
        raise MalformedCursor
    return position
