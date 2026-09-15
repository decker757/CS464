"""Keyset cursors.

Pure functions, no database. Worth testing directly because a cursor bug shows
up as rows quietly missing from a page rather than as an error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.errors import MalformedCursor
from core.paging import decode_cursor, encode_cursor


def test_a_cursor_round_trips() -> None:
    moment = datetime(2026, 9, 15, 12, 30, 45, 123456, tzinfo=UTC)
    row_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(moment, row_id)) == (moment, row_id)


def test_microseconds_survive() -> None:
    """Both entries of a movement carry the identical timestamp, so the
    sub-second part is often all that separates two adjacent pages."""
    first = datetime(2026, 9, 15, 12, 0, 0, 1, tzinfo=UTC)
    second = first + timedelta(microseconds=1)

    assert encode_cursor(first, uuid.uuid4()) != encode_cursor(second, uuid.uuid4())


def test_the_cursor_is_opaque() -> None:
    """It encodes a position in one ordering. A client that builds one by hand
    has invented a position this service never promised to honour."""
    cursor = encode_cursor(datetime.now(UTC), uuid.uuid4())

    assert "|" not in cursor
    assert ":" not in cursor


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not base64 at all !!",
        "Zm9vYmFy",  # valid base64, no separator
        "MjAyNi0wOS0xNXxub3QtYS11dWlk",  # valid base64, unparseable uuid
    ],
)
def test_anything_we_did_not_issue_is_one_error(bad: str) -> None:
    """Every failure mode collapses to MalformedCursor deliberately. The
    difference between bad base64 and a bad UUID is not something a client can
    act on, and describing it precisely only helps someone probing what the
    value is made of."""
    with pytest.raises(MalformedCursor):
        decode_cursor(bad)


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """A naive value would compare against an aware column and raise inside the
    query rather than here."""
    naive = "2026-09-15T12:00:00"
    row_id = uuid.uuid4()
    import base64

    raw = f"{naive}|{row_id}".encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    decoded, _ = decode_cursor(cursor)

    assert decoded.tzinfo is not None
