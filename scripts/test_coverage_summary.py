"""The backend coverage figure, [F-12] #342.

One number out of six suites, so the arithmetic that produces it is checked
rather than trusted: a wrong percentage in a report is not something CI would
otherwise notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coverage_summary import Totals, read_totals, to_markdown


def test_a_suite_weighs_as_much_as_it_has_code() -> None:
    """The overall figure is every line over every statement, not the mean.

    Two suites of the same size cannot tell the two apart — both answers are
    45% — so the sizes here are deliberately lopsided: 52 of 102 is 51.0%,
    where averaging 100% and 50% would say 75%.
    """
    table = to_markdown({"audit": Totals(2, 2), "ledger": Totals(50, 100)})

    assert "51.0%" in table


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


def test_a_report_is_named_after_the_suite_that_wrote_it(tmp_path: Path) -> None:
    """The workflow writes `coverage-<service>.json`; the row carries that name.

    The two halves are set in different files — the `--cov-report` argument in
    `ci-backend.yml`, and the prefix stripped here — so nothing but this says
    they have to agree.
    """
    report = tmp_path / "coverage-ledger_service.json"
    report.write_text(json.dumps({"totals": {"covered_lines": 3, "num_statements": 4}}))

    suite, totals = read_totals(report)

    assert suite == "ledger_service"
    assert totals == Totals(3, 4)
