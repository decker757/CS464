"""`as_utc`: a naive value is stamped UTC, an aware one is left alone. [#296]"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from core.clock import as_utc


def test_a_naive_value_is_stamped_utc_with_the_same_wall_clock() -> None:
    result = as_utc(datetime(2026, 3, 4, 5, 6, 7))

    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_an_aware_utc_value_is_returned_unchanged() -> None:
    value = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

    assert as_utc(value) == value
    assert as_utc(value).utcoffset() == timedelta(0)


def test_an_aware_value_with_an_offset_is_not_converted() -> None:
    value = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone(timedelta(hours=8)))

    result = as_utc(value)

    assert result.utcoffset() == timedelta(hours=8)
    assert result.hour == 5
