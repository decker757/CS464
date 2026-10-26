"""`as_utc`: a naive value is stamped UTC, not read as local time. [#320]
`has_utc_instant`: a time with no instant in UTC is caught before asyncpg. [#490]

No database, no HTTP. The twin of `ledger_service/unit_test/core/test_clock.py`;
copied, not shared, like the function.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.clock import as_utc, has_utc_instant


@pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="time.tzset does not exist on Windows"
)
def test_a_naive_value_is_stamped_utc_in_a_zone_that_is_not_utc() -> None:
    """In a UTC zone `astimezone(UTC)` returns the same wall clock as stamping,
    so a test run there cannot tell them apart. Singapore can."""
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


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # The last hour of year 9999 at UTC-5 is year 10000 in UTC.
        (datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-5))), False),
        # Year 1 at UTC+1 is year 0 in UTC.
        (datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))), False),
        # The ends themselves, in UTC, still pass: nothing real is narrowed.
        (datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC), True),
        (datetime(1, 1, 1, 0, 0, tzinfo=UTC), True),
        (datetime(2027, 1, 5, 12, 0, tzinfo=timezone(timedelta(hours=8))), True),
    ],
)
def test_only_a_time_with_no_instant_in_utc_lacks_one(
    moment: datetime, expected: bool
) -> None:
    """asyncpg converts every bound to UTC, and raises past either end of
    `datetime`'s range; this is the check that turns that into a 422 (#490)."""
    assert has_utc_instant(moment) is expected
