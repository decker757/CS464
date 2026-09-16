"""Keyset cursors for the administrative user list. [4.1] #13

The cursor format itself is in `shared/paging.py`, which the audit and ledger
services already page with. What stays here is this service's error vocabulary:
`shared.paging.decode_cursor` returns None for a value it did not issue, and
this is where that becomes a `MalformedCursor` the controller turns into a 400.

**Why keyset and not OFFSET**, on a table that is not append-only. `auth.users`
can be updated — a role change, a suspension — but it only ever *grows* at the
newest end, because the sole way a row appears is a registration. That is the
condition OFFSET breaks under: somebody registering between page 1 and page 2
pushes the window down, so the last account of page 1 comes back as the first
of page 2 and the one behind it is never seen. An administrator paging through
users to find the one they are investigating is precisely the reader who would
never notice.

A cursor names a position instead — "everything strictly older than
(created_at, id)" — so pages stay stable no matter who registers while the
list is being read. An update cannot move a row in this ordering, because
neither `created_at` nor `id` is ever written twice.

`created_at` is not unique: two registrations can land in the same microsecond,
and under a `server_default` of `now()` two in one transaction would share a
timestamp exactly. That is why the id is in the cursor at all. See
`shared/paging.py` for the rest of the cases.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
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
