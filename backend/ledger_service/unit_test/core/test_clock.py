"""`as_utc`: a naive value is stamped UTC, an aware one is left alone. [#296]"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.clock import as_utc


def test_a_naive_value_is_stamped_utc_with_the_same_wall_clock() -> None:
    result = as_utc(datetime(2026, 3, 4, 5, 6, 7))

    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC), id="utc"),
        pytest.param(
            datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone(timedelta(hours=8))),
            id="+08:00",
        ),
    ],
)
def test_an_aware_value_is_returned_unchanged(value: datetime) -> None:
    result = as_utc(value)

    assert result == value
    assert result.utcoffset() == value.utcoffset()
    assert result.hour == value.hour


@pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="time.tzset does not exist on Windows"
)
def test_a_naive_value_is_stamped_utc_in_a_zone_that_is_not_utc() -> None:
    """In a UTC zone `astimezone(UTC)` returns the same wall clock as stamping,
    so the test above cannot tell them apart. Singapore can. [#320]"""
    original_zone = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Singapore"
    time.tzset()
    try:
        naive = datetime(2026, 3, 4, 5, 6, 7)
        assert naive.astimezone().utcoffset() == timedelta(hours=8), "zone not set"
        result = as_utc(naive)
    finally:
        if original_zone is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = original_zone
        time.tzset()

    assert result == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
