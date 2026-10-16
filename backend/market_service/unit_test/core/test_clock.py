"""`as_utc`: a naive value is stamped UTC, not read as local time. [#320]

No database, no HTTP. The twin of `ledger_service/unit_test/core/test_clock.py`;
copied, not shared, like the function.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from core.clock import as_utc


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
