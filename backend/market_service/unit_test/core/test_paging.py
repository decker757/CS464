"""Keyset cursors for the public browse [X-1] #104 and the admin overview #210.
No database, no HTTP.

Tested directly because a cursor bug shows up as markets quietly missing from a
page, which an end-to-end test is least likely to notice.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.errors import MalformedCursor
from core.paging import (
    BrowsePosition,
    OverviewPosition,
    decode_browse_cursor,
    decode_overview_cursor,
    encode_browse_cursor,
    encode_overview_cursor,
)

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


# --- the admin overview's cursor, #210 --------------------------------------
_MARKET_ID = "410465f3-2852-4833-964b-f42e23b8227c"


@pytest.mark.parametrize("settled", [False, True], ids=["active", "settled"])
@pytest.mark.parametrize(
    "close_time",
    [
        datetime(2026, 12, 31, 16, 0, 0, 123456, tzinfo=timezone(timedelta(hours=8))),
        None,
    ],
    ids=["dated", "undated"],
)
def test_an_overview_cursor_round_trips(
    settled: bool, close_time: datetime | None
) -> None:
    """#210: exactly, microseconds and both groups included, and a missing close
    time stays missing: "the cursor encodes the nulls-last group as well as the
    value, and a page boundary inside the drafts can be named"."""
    position = OverviewPosition(
        settled=settled, close_time=close_time, market_id=uuid.UUID(_MARKET_ID)
    )

    assert decode_overview_cursor(encode_overview_cursor(position)) == position


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not base64 at all !!",
        _cursor_of(_WHEN, _MARKET_ID),
        _cursor_of("yes", "1", _WHEN, _MARKET_ID),
        _cursor_of("0", "yes", "", _MARKET_ID),
        _cursor_of("0", "1", "", _MARKET_ID),
        _cursor_of("0", "0", _WHEN, _MARKET_ID),
        _cursor_of("0", "1", "not-a-timestamp", _MARKET_ID),
        _cursor_of("0", "1", _WHEN, "not-a-uuid"),
    ],
    ids=[
        "empty",
        "not_base64",
        "another_services_two_field_cursor",
        "a_group_flag_that_is_not_1_or_0",
        "a_close_time_flag_that_is_not_1_or_0",
        "flagged_dated_with_no_close_time",
        "flagged_undated_with_a_close_time",
        "bad_close_time",
        "bad_id",
    ],
)
def test_anything_this_service_did_not_issue_is_one_overview_error(bad: str) -> None:
    """#210: "malformed cursor is 400 malformed_cursor". Every bad value is one
    MalformedCursor, never a ValueError surfacing as a 500, and never a position
    whose flag and close time disagree."""
    with pytest.raises(MalformedCursor):
        decode_overview_cursor(bad)


def test_a_browse_cursor_and_an_overview_cursor_are_not_interchangeable() -> None:
    """Both are four fields on one format, so only their contents tell them
    apart. Each decoder refuses the other list's cursor rather than reading it
    as a position in a different order."""
    browse_cursor = encode_browse_cursor(
        BrowsePosition(
            as_of=datetime(2026, 10, 4, 9, 15, tzinfo=UTC),
            is_open=True,
            sort_at=datetime(2026, 11, 1, tzinfo=UTC),
            market_id=uuid.UUID(_MARKET_ID),
        )
    )
    overview_cursor = encode_overview_cursor(
        OverviewPosition(
            settled=False,
            close_time=datetime(2026, 12, 31, tzinfo=UTC),
            market_id=uuid.UUID(_MARKET_ID),
        )
    )

    with pytest.raises(MalformedCursor):
        decode_overview_cursor(browse_cursor)
    with pytest.raises(MalformedCursor):
        decode_browse_cursor(overview_cursor)


def test_a_naive_close_time_in_an_overview_cursor_is_read_as_utc() -> None:
    """A naive value would compare against an aware column and raise inside the
    query, as a 500, rather than here."""
    cursor = _cursor_of("0", "1", "2026-12-31T16:00:00", _MARKET_ID)

    position = decode_overview_cursor(cursor)

    assert position.close_time == datetime(2026, 12, 31, 16, 0, tzinfo=UTC)
