"""Keyset cursors and the page size for the market lists: the public browse
[X-1] #104 and the admin overview [2.1] #210.

The format is `shared/paging.py`'s (ADR 0012); this reads its fields as a
position in one list or the other and turns a None into `MalformedCursor`.
Each list has four fields of its own, because each order has its own groups
and keys. One caller each, so the shapes stay here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from core.clock import as_utc
from core.config import get_settings
from core.errors import MalformedCursor
from shared import paging as _shared

_BROWSE_FIELD_COUNT = 4
_OVERVIEW_FIELD_COUNT = 4
_TRUE = "1"
_FALSE = "0"


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


@dataclass(frozen=True)
class OverviewPosition:
    """The last market an overview page showed. [2.1] #210.

    `settled` is its group: settled markets sort after every other. A
    `close_time` of None sorts after every dated market in the same group.
    """

    settled: bool
    close_time: datetime | None
    market_id: uuid.UUID


def _flag(value: bool) -> str:
    """A bool as its cursor field."""
    return _TRUE if value else _FALSE


def _flag_of(text: str) -> bool:
    """A bool from its cursor field. Strict: anything but the two flags is refused."""
    if text == _TRUE:
        return True
    if text == _FALSE:
        return False
    raise ValueError(f"not a 0/1 flag: {text!r}")


def _close_time_of(has_close_time: bool, text: str) -> datetime | None:
    """The close time from its field, which is empty exactly when there is none."""
    if not has_close_time:
        if text:
            raise ValueError(f"a close time on a position without one: {text!r}")
        return None
    return as_utc(datetime.fromisoformat(text))


def encode_browse_cursor(position: BrowsePosition) -> str:
    """The position just after `position.market_id`, as an opaque string."""
    return _shared.encode_fields(
        position.as_of.isoformat(),
        _flag(position.is_open),
        position.sort_at.isoformat(),
        str(position.market_id),
    )


def decode_browse_cursor(cursor: str) -> BrowsePosition:
    """Read a cursor `encode_browse_cursor` issued, or raise MalformedCursor.

    Every kind of bad value is one error: a client cannot act on the
    difference. A naive timestamp is read as UTC, or it would raise inside the
    query instead of here.
    """
    fields = _shared.decode_fields(cursor, count=_BROWSE_FIELD_COUNT)
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


def encode_overview_cursor(position: OverviewPosition) -> str:
    """The position just after `position.market_id`, as an opaque string."""
    if position.close_time is None:
        has_close_time, close_time = _FALSE, ""
    else:
        has_close_time, close_time = _TRUE, position.close_time.isoformat()
    return _shared.encode_fields(
        _flag(position.settled),
        has_close_time,
        close_time,
        str(position.market_id),
    )


def decode_overview_cursor(cursor: str) -> OverviewPosition:
    """Read a cursor `encode_overview_cursor` issued, or raise MalformedCursor.

    Every kind of bad value is one error, a browse cursor included. A naive
    timestamp is read as UTC, for the browse cursor's reason.
    """
    fields = _shared.decode_fields(cursor, count=_OVERVIEW_FIELD_COUNT)
    if fields is None:
        raise MalformedCursor

    settled, has_close_time, close_time, market_id = fields
    try:
        return OverviewPosition(
            settled=_flag_of(settled),
            close_time=_close_time_of(_flag_of(has_close_time), close_time),
            market_id=uuid.UUID(market_id),
        )
    except ValueError as error:
        raise MalformedCursor from error


def page_size_of(limit: int | None) -> int:
    """The page size for a requested `limit`: the default when none, the
    ceiling when more. Clamped rather than refused, as the audit feed and the
    user list do: a caller asking for more than the ceiling wants as much as it
    can get."""
    settings = get_settings()
    return min(limit or settings.default_page_size, settings.max_page_size)
