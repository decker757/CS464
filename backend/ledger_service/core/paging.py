"""Keyset cursors for a user's ledger history, and for the leaderboard.

The format is `shared/paging.py`'s; this turns its None into `MalformedCursor`.
Keyset, not OFFSET: the history grows at the newest end, so OFFSET pages would
repeat a row and skip another as entries arrive. The id is in the cursor
because `created_at` is not unique.

The leaderboard's cursor is a different shape: a generation and the last rank
served from it. DECISIONS.md, "A leaderboard cursor names its generation; the
current and previous generations are served, and an older one is `409
leaderboard_refreshed`".
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from core.errors import MalformedCursor
from shared import paging as _shared

encode_cursor = _shared.encode_cursor

# `leaderboard_entries.rank` is an Integer column (4 bytes). A larger rank
# bound into the page query is refused by asyncpg, which would be a 500.
_MAX_RANK = 2**31 - 1

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


def encode_rank_cursor(generation_id: uuid.UUID, last_rank: int) -> str:
    """The position just after `last_rank` in one leaderboard generation."""
    return _shared.encode_fields(str(generation_id), str(last_rank))


def decode_rank_cursor(cursor: str) -> tuple[uuid.UUID, int]:
    """Read a cursor `encode_rank_cursor` issued, or raise MalformedCursor.

    A rank below 1 or above the column's maximum is refused here, because
    this service never issues one and Postgres must not be the one to say so.
    """
    fields = _shared.decode_fields(cursor, count=2)
    if fields is None:
        raise MalformedCursor

    generation_text, rank_text = fields
    try:
        generation_id = uuid.UUID(generation_text)
        last_rank = int(rank_text)
    except ValueError:
        raise MalformedCursor from None

    if not 1 <= last_rank <= _MAX_RANK:
        raise MalformedCursor
    return generation_id, last_rank


def page_cut_of(
    fetched: list[_Row], limit: int, *, cursor_of: Callable[[_Row], str]
) -> tuple[list[_Row], str | None]:
    """The first `limit` of the rows fetched with `limit + 1`, and the cursor
    after them: None unless the extra row proves there is more.

    `cursor_of` turns the page's last row into the next cursor. `limit` is at
    least 1, which every caller's `_page_size` guarantees.
    """
    page = fetched[:limit]
    next_cursor = cursor_of(page[-1]) if len(fetched) > limit else None
    return page, next_cursor
