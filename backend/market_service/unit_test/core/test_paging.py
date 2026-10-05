"""Keyset cursors for the public browse. [X-1] #104. No database, no HTTP.

Tested directly because a cursor bug shows up as markets quietly missing from a
page, which an end-to-end test is least likely to notice.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.errors import MalformedCursor
from core.paging import BrowsePosition, decode_browse_cursor, encode_browse_cursor

_WHEN = "2026-10-04T09:15:00+00:00"


def _cursor_of(*fields: str) -> str:
    """A cursor built by hand from the raw format, as a forger would."""
    raw = "|".join(fields)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@pytest.mark.parametrize("is_open", [True, False], ids=["trading", "stopped"])
def test_a_browse_cursor_round_trips(is_open: bool) -> None:
    """Exactly, microseconds included, or a page repeats or skips a
    market. Both groups, so an encoder that always writes one flag fails."""
    singapore = timezone(timedelta(hours=8))
    position = BrowsePosition(
        as_of=datetime(2026, 10, 4, 9, 15, 0, 123456, tzinfo=singapore),
        is_open=is_open,
        sort_at=datetime(2026, 11, 1, 12, 0, 0, 654321, tzinfo=UTC),
        market_id=uuid.uuid4(),
    )

    assert decode_browse_cursor(encode_browse_cursor(position)) == position


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not base64 at all !!",
        _cursor_of(_WHEN, str(uuid.uuid4())),
        _cursor_of(_WHEN, "yes", _WHEN, str(uuid.uuid4())),
        _cursor_of("not-a-timestamp", "1", _WHEN, str(uuid.uuid4())),
        _cursor_of(_WHEN, "1", "not-a-timestamp", str(uuid.uuid4())),
        _cursor_of(_WHEN, "1", _WHEN, "not-a-uuid"),
    ],
    ids=[
        "empty",
        "not_base64",
        "another_services_two_field_cursor",
        "a_flag_that_is_not_1_or_0",
        "bad_as_of",
        "bad_sort_at",
        "bad_id",
    ],
)
def test_anything_this_service_did_not_issue_is_one_error(bad: str) -> None:
    """Every kind of bad value is one MalformedCursor, never a ValueError that
    would surface as a 500."""
    with pytest.raises(MalformedCursor):
        decode_browse_cursor(bad)


def test_a_naive_timestamp_in_a_cursor_is_read_as_utc() -> None:
    """A naive value would compare against an aware column and raise inside the
    query, as a 500, rather than here."""
    cursor = _cursor_of(
        "2026-10-04T09:15:00", "0", "2026-10-01T00:00:00", str(uuid.uuid4())
    )

    position = decode_browse_cursor(cursor)

    assert position.as_of == datetime(2026, 10, 4, 9, 15, tzinfo=UTC)
    assert position.sort_at == datetime(2026, 10, 1, tzinfo=UTC)
