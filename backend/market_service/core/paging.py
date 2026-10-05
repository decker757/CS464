"""Keyset cursors for the public market browse. [X-1] #104

The format is `shared/paging.py`'s (ADR 0012); this reads its fields as a
browse position and turns a None into `MalformedCursor`. Four fields, not the
shared pair, because the browse order has two groups with a key each, and the
grouping instant rides along. One caller, so the shape stays here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.clock import as_utc
from core.errors import MalformedCursor
from shared import paging as _shared

_FIELD_COUNT = 4
_TRADING = "1"
_STOPPED = "0"


@dataclass(frozen=True)
class BrowsePosition:
    """The last market a page showed, and the instant the browse is grouped by.

    `as_of` is the first page's clock; `is_open` is the market's group at that
    instant; `sort_at` is its key in that group.
    """

    as_of: datetime
    is_open: bool
    sort_at: datetime
    market_id: uuid.UUID


def _flag_of(text: str) -> bool:
    """`is_open` from its field. Strict: anything but the two flags is refused."""
    if text == _TRADING:
        return True
    if text == _STOPPED:
        return False
    raise ValueError(f"not a group flag: {text!r}")


def encode_browse_cursor(position: BrowsePosition) -> str:
    """The position just after `position.market_id`, as an opaque string."""
    return _shared.encode_fields(
        position.as_of.isoformat(),
        _TRADING if position.is_open else _STOPPED,
        position.sort_at.isoformat(),
        str(position.market_id),
    )


def decode_browse_cursor(cursor: str) -> BrowsePosition:
    """Read a cursor `encode_browse_cursor` issued, or raise MalformedCursor.

    Every kind of bad value is one error: a client cannot act on the
    difference. A naive timestamp is read as UTC, or it would raise inside the
    query instead of here.
    """
    fields = _shared.decode_fields(cursor, count=_FIELD_COUNT)
    if fields is None:
        raise MalformedCursor

    as_of, flag, sort_at, market_id = fields
    try:
        return BrowsePosition(
            as_of=as_utc(datetime.fromisoformat(as_of)),
            is_open=_flag_of(flag),
            sort_at=as_utc(datetime.fromisoformat(sort_at)),
            market_id=uuid.UUID(market_id),
        )
    except ValueError as error:
        raise MalformedCursor from error
