"""Keyset cursors. No database, no HTTP.

Worth testing directly rather than only through a page of results: a cursor bug
shows up as rows quietly missing from a page, which is the failure mode an
audit log can least afford and an end-to-end test is least likely to notice.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.errors import MalformedCursor
from core.paging import decode_cursor, encode_cursor


def test_a_cursor_round_trips() -> None:
    when = datetime(2026, 9, 15, 10, 47, 30, 123456, tzinfo=UTC)
    row_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(when, row_id)) == (when, row_id)


def test_microseconds_survive() -> None:
    """Two entries a microsecond apart are two positions, not one.

    Truncating here would make the cursor ambiguous between them, which is how
    a paged read starts repeating or skipping a row.
    """
    first = datetime(2026, 9, 15, 10, 47, 30, 1, tzinfo=UTC)
    second = first + timedelta(microseconds=1)
    row_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(first, row_id))[0] != decode_cursor(
        encode_cursor(second, row_id)
    )[0]


def test_the_offset_is_preserved() -> None:
    """A cursor read back as the wrong instant would page from the wrong place."""
    when = datetime(2026, 9, 15, 10, 47, 30, tzinfo=UTC)

    decoded, _ = decode_cursor(encode_cursor(when, uuid.uuid4()))

    assert decoded == when
    assert decoded.tzinfo is not None


def test_a_cursor_is_url_safe() -> None:
    """It travels as a query parameter, so it cannot need escaping."""
    cursor = encode_cursor(datetime.now(UTC), uuid.uuid4())

    assert set(cursor) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-base64-at-all!!",
        base64.urlsafe_b64encode(b"no-separator-here").decode(),
        base64.urlsafe_b64encode(b"2026-09-15T10:47:30+00:00|not-a-uuid").decode(),
        base64.urlsafe_b64encode(b"not-a-timestamp|" + str(uuid.uuid4()).encode()).decode(),
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
    ],
)
def test_anything_this_service_did_not_issue_is_refused(bad: str) -> None:
    """Every failure collapses to one error on purpose.

    The difference between bad base64, a missing separator and an unparseable
    UUID is not something a client can act on differently, and describing it
    precisely only helps someone working out what the value is made of.
    """
    with pytest.raises(MalformedCursor):
        decode_cursor(bad)


def test_a_naive_timestamp_in_a_cursor_is_read_as_utc() -> None:
    """Defensive. A naive value would reach the query and raise there, where it
    would surface as a 500 rather than as the 400 this is."""
    raw = f"2026-09-15T10:47:30|{uuid.uuid4()}"
    cursor = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    decoded, _ = decode_cursor(cursor)

    assert decoded.tzinfo is not None
