"""Keyset cursors. No database, no HTTP.

Tested directly because a cursor bug shows up as rows quietly missing from a
page, which an end-to-end test is least likely to notice.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.errors import MalformedCursor
from core.paging import decode_cursor, encode_cursor


def test_a_cursor_round_trips() -> None:
    """Exactly, microseconds and offset included, or a page repeats or skips a row.

    A non-UTC offset, because a naive value decodes as UTC: an encoder that
    dropped the offset would still pass with a UTC timestamp.
    """
    singapore = timezone(timedelta(hours=8))
    when = datetime(2026, 9, 15, 10, 47, 30, 123456, tzinfo=singapore)
    row_id = uuid.uuid4()

    assert decode_cursor(encode_cursor(when, row_id)) == (when, row_id)


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
    """Every kind of bad value collapses to one error on purpose."""
    with pytest.raises(MalformedCursor):
        decode_cursor(bad)


def test_a_naive_timestamp_in_a_cursor_is_read_as_utc() -> None:
    """A naive value would otherwise raise in the query, as a 500."""
    raw = f"2026-09-15T10:47:30|{uuid.uuid4()}"
    cursor = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    decoded, _ = decode_cursor(cursor)

    assert decoded.tzinfo is not None
