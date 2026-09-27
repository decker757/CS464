"""Keyset cursors for the administrative user list. [4.1] #13

The format is in `shared/paging.py` (ADR 0012); this turns its None into
MalformedCursor. Keyset, not OFFSET: a registration between two pages would
shift an OFFSET window, repeating one account and hiding another. Updates never
move a row, since `created_at` and `id` are written once; the id breaks ties.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Return the (created_at, id) position a cursor names.

    Raises MalformedCursor for every kind of bad value alike: a client cannot
    act on the difference, and naming it only helps someone probing the format.
    """
    position = _shared.decode_cursor(cursor)
    if position is None:
        raise MalformedCursor
    return position
