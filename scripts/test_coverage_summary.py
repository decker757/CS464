"""The backend coverage figure, [F-12] #342.

One number out of six suites, so the arithmetic that produces it is checked
rather than trusted: a wrong percentage in a report is not something CI would
otherwise notice.
"""

from __future__ import annotations

import pytest

from coverage_summary import Totals, to_markdown


def test_the_overall_figure_is_lines_over_statements_across_every_suite() -> None:
    table = to_markdown({"auth": Totals(8, 10), "market": Totals(1, 10)})

    # 9 of 20, not the average of 80% and 10%.
    assert "45.0%" in table


def test_a_suite_weighs_as_much_as_it_has_code() -> None:
    small_and_perfect = to_markdown({"audit": Totals(2, 2), "ledger": Totals(50, 100)})

    assert "51.0%" in small_and_perfect


def test_every_suite_is_named_with_its_own_figure() -> None:
    table = to_markdown({"auth": Totals(8, 10), "market": Totals(1, 10)})

    assert "auth" in table
    assert "80.0%" in table
    assert "market" in table
    assert "10.0%" in table


def test_a_suite_with_no_statements_reads_as_nothing_to_measure() -> None:
    table = to_markdown({"realtime": Totals(0, 0)})

    assert "n/a" in table


def test_no_suite_at_all_is_an_error_rather_than_a_hundred_percent() -> None:
    with pytest.raises(ValueError, match="no coverage"):
        to_markdown({})


def test_the_overall_row_counts_the_suites_it_actually_read() -> None:
    """If a job failed to upload, the figure must not still claim all six."""
    two_of_them = to_markdown({"auth": Totals(8, 10), "market": Totals(1, 10)})

    assert "All 2 suites" in two_of_them
