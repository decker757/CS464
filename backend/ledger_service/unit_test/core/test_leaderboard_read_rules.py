"""The leaderboard read's pure rules: when a ranking is stale, and the rank
cursor. [L-1] #38, [L-3] #40

DECISIONS.md, "A refresh's `computed_at` is Python's clock, read before the
valuation; `stale` is derived on read against the same clock":
`stale = now - computed_at > 2 × LEADERBOARD_REFRESH_SECONDS`. And "A
leaderboard cursor names its generation; the current and previous generations
are served, and an older one is `409 leaderboard_refreshed`": the cursor is
`encode_fields(generation_id, last_rank)`, and one that does not decode is
`400 malformed_cursor`. No database.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.errors import MalformedCursor
from core.leaderboard import is_stale
from core.paging import decode_rank_cursor, encode_rank_cursor
from shared.paging import encode_cursor, encode_fields
from unit_test.leaderboard_read_fixtures import RANK_OVERFLOW, rank_cursor

_COMPUTED_AT = datetime(2026, 10, 12, 9, 0, 0, 123456, tzinfo=UTC)
_GENERATION = uuid.UUID("2f4e0c1a-9b7d-4e5f-8a6b-1c2d3e4f5a6b")


# =========================================================================
# stale
# =========================================================================
@pytest.mark.parametrize(
    ("age", "stale"),
    [
        (timedelta(seconds=120), False),
        (timedelta(seconds=120, microseconds=1), True),
    ],
    ids=["exactly-twice-the-interval", "one-microsecond-past"],
)
def test_stale_is_false_at_exactly_twice_the_interval_and_true_one_microsecond_past(
    age: timedelta, stale: bool
) -> None:
    """"more than two refresh intervals old". Catches: `>=` for `>` (the exact
    edge reads stale); one interval rather than two (120 s reads stale);
    comparing whole seconds (the microsecond past reads fresh)."""
    assert (
        is_stale(_COMPUTED_AT, now=_COMPUTED_AT + age, refresh_seconds=60) is stale
    )


# =========================================================================
# The rank cursor
# =========================================================================
def test_a_rank_cursor_round_trips_and_is_the_decided_format() -> None:
    """The format is the decision's: `encode_fields(generation_id, last_rank)`.
    Catches: another format, or the two fields swapped."""
    cursor = encode_rank_cursor(_GENERATION, 7)

    assert cursor == encode_fields(str(_GENERATION), "7")
    assert decode_rank_cursor(cursor) == (_GENERATION, 7)


def _unpadded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


_NOT_ISSUED = {
    "garbage": "nonsense",
    "not-base64": "!!!",
    "one-field": encode_fields(str(_GENERATION)),
    "three-fields": encode_fields(str(_GENERATION), "1", "2"),
    "generation-not-a-uuid": encode_fields("not-a-uuid", "1"),
    "rank-not-an-integer": rank_cursor(_GENERATION, "abc"),
    "rank-a-fraction": rank_cursor(_GENERATION, "1.5"),
    "rank-zero": rank_cursor(_GENERATION, 0),
    "rank-negative": rank_cursor(_GENERATION, -1),
    "rank-above-the-column": rank_cursor(_GENERATION, RANK_OVERFLOW),
    "fields-swapped": encode_fields("7", str(_GENERATION)),
    "history-cursor": encode_cursor(_COMPUTED_AT, _GENERATION),
    "not-utf-8": _unpadded(b"\xff\xfe|1"),
}


@pytest.mark.parametrize("cursor", list(_NOT_ISSUED.values()), ids=list(_NOT_ISSUED))
def test_a_rank_cursor_we_did_not_issue_is_malformed(cursor: str) -> None:
    """Every shape the server never issues is one error. Catches: a decoder
    that skips the UUID or the integer check, accepts a rank below 1 (the
    column's CHECK is `rank > 0`) or above the Integer column (asyncpg would
    refuse the bound parameter: a 500), lets `UnicodeDecodeError` escape, or
    returns None instead of raising."""
    with pytest.raises(MalformedCursor):
        decode_rank_cursor(cursor)
