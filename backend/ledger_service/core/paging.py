"""Keyset cursors for a user's ledger history.

The format is `shared/paging.py`'s; this turns its None into `MalformedCursor`.
Keyset, not OFFSET: the history grows at the newest end, so OFFSET pages would
repeat a row and skip another as entries arrive. The id is in the cursor
because `created_at` is not unique.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor

#: One row's place in the feed: the (created_at, id) pair the ordering, the
#: cursor and the running balance all agree on.
Position = tuple[datetime, uuid.UUID]

_Row = TypeVar("_Row")


def decode_cursor(cursor: str) -> Position:
    """Read a cursor this service issued, or raise MalformedCursor.

    Every failure is one error: the client cannot act on the difference, and
    detail only helps someone probing the format.
    """
    position = _shared.decode_cursor(cursor)
    if position is None:
        raise MalformedCursor
    return position


def page_cut_of(
    fetched: list[_Row], limit: int, *, cursor_of: Callable[[_Row], str]
) -> tuple[list[_Row], str | None]:
    """The first `limit` of the rows fetched with `limit + 1`, and the cursor
    after them: None unless the extra row proves there is more.

    `cursor_of` turns the page's last row into the next cursor.
    """
    page = fetched[:limit]
    next_cursor = cursor_of(page[-1]) if len(fetched) > limit else None
    return page, next_cursor
