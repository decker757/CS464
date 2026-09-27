"""Keyset cursors for the audit feed. [4.3] #15

The format is in `shared/paging.py` (ADR 0012); this turns its None into
MalformedCursor. Keyset, not OFFSET: rows appended at the newest end shift an
OFFSET window, so a reader would see one row twice and skip another. The id is
in the cursor because `occurred_at` is not unique.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Return the (occurred_at, id) position a cursor names.

    Raises MalformedCursor for every kind of bad value alike: a client cannot
    act on the difference, and naming it only helps someone probing the format.
    """
    position = _shared.decode_cursor(cursor)
    if position is None:
        raise MalformedCursor
    return position
