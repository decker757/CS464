"""The keyset cursor format. [F-6] #76

Shared between the audit feed and the ledger history, which page identically
because they are the same shape of problem: an append-only table read newest
first, where OFFSET would let rows arriving mid-read shift the window and show
a reader the same row twice while hiding the one behind it.

A cursor is a list of text fields joined by `|`, base64'd. `encode_fields` and
`decode_fields` are that format for any number of fields; `encode_cursor` and
`decode_cursor` are the `(timestamp, id)` pair the feeds use, built on them.
The market browse ([X-1] #104) reads four fields in its own `core/paging.py`:
one caller, so that shape is not here (ADR 0012).

The id is not decoration — neither timestamp is unique. Two services can append
in the same microsecond, and in the ledger every transaction writes at least
two entries carrying the identical timestamp by construction, because they are
two halves of one movement. A cursor that cannot tell them apart either loses
one or repeats it forever.

**What this module deliberately does not own: the error.** The decoders return
None rather than raising, and each service's `core/paging.py` turns that into
its own `MalformedCursor`. The alternative would be a shared exception base
class, which is the "sharing `core`" that ADR 0005 ruled out — and the point of
each service having its own error hierarchy is that a ledger error cannot be
caught by an `except` in the audit service.

The format is opaque on purpose. A client that constructs one by hand has
invented a position no service ever promised to honour.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

_SEPARATOR = "|"


def encode_fields(*fields: str) -> str:
    """Join `fields` into one opaque, URL-safe string.

    No field may contain `|`. Every caller passes ISO timestamps, UUIDs and
    flags, none of which can.
    """
    raw = _SEPARATOR.join(fields)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_fields(cursor: str, *, count: int) -> list[str] | None:
    """The `count` fields `encode_fields` joined, or None if `cursor` is not that.

    Bad base64, bytes that are not UTF-8 and a different number of fields all
    collapse to None: a client cannot act on the difference, and describing it
    only helps someone probing what the value is made of.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None

    fields = raw.split(_SEPARATOR)
    if len(fields) != count:
        return None
    return fields


def encode_cursor(timestamp: datetime, row_id: uuid.UUID) -> str:
    """The position just after `row_id`, as an opaque string."""
    return encode_fields(timestamp.isoformat(), str(row_id))


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    """Read a cursor `encode_cursor` issued, or None if it is not one."""
    fields = decode_fields(cursor, count=2)
    if fields is None:
        return None

    timestamp, row_id = fields
    try:
        moment = datetime.fromisoformat(timestamp)
        # A naive value would compare against an aware column and raise inside
        # the query instead of here.
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, uuid.UUID(row_id)
    except ValueError:
        return None
