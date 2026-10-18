"""One backend coverage figure out of the six suites' reports. [F-12] #342.

Each suite runs as its own job, as its own role, against its own schema, and
writes a `coverage json`. This adds them up.

Not `coverage combine`: every service has its own `core/` and `service/`, so
combining six reports would merge files that only share a path. Summing the
totals cannot confuse one service's file for another's.

What it therefore measures is each suite against its own directory. `--cov=.`
is scoped to where pytest ran, so `shared/` is measured once, by its own
suite, and not again by the five services that import it — which is why
`shared/config.py` can read 0% while four services import it. Do not "fix"
that by adding `--cov=../shared` to the services: that is what would double
count it, and the headline would start counting the same statements six
times.

    python3 scripts/coverage_summary.py coverage-*.json >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Totals:
    covered_lines: int
    num_statements: int


def read_totals(report: Path) -> tuple[str, Totals]:
    """The suite's name and its totals, from one `coverage json` file.

    The name is the file's, minus `coverage-` and the extension: the workflow
    writes `coverage-auth_service.json`.
    """
    totals = json.loads(report.read_text())["totals"]
    suite = report.stem.removeprefix("coverage-")
    return suite, Totals(totals["covered_lines"], totals["num_statements"])


def _percentage(totals: Totals) -> str:
    """A suite's figure, or `n/a` where there is nothing to measure."""
    if totals.num_statements == 0:
        return "n/a"
    return f"{100 * totals.covered_lines / totals.num_statements:.1f}%"


def to_markdown(suites: Mapping[str, Totals]) -> str:
    """The table for the job summary: one row per suite, then the whole backend.

    The overall figure is every covered line over every statement, so a suite
    counts for as much code as it has — not the average of six percentages,
    which would let a tiny suite outvote the ledger.
    """
    if not suites:
        raise ValueError('Found no coverage reports. Did the test jobs upload them?')

    rows = [f'| {suite} | {_percentage(totals)} |' for suite, totals in sorted(suites.items())]
    overall = Totals(
        covered_lines=sum(totals.covered_lines for totals in suites.values()),
        num_statements=sum(totals.num_statements for totals in suites.values()),
    )
    return '\n'.join(
        [
            '### Backend test coverage',
            '',
            '| Suite | Lines covered |',
            '| --- | --- |',
            *rows,
            # Counted, not written as six: a suite whose job failed to upload
            # would otherwise leave the figure claiming a coverage it never read.
            f'| **All {len(suites)} suites** | **{_percentage(overall)}** |',
            '',
            f'{overall.covered_lines} of {overall.num_statements} statements. '
            'End-to-end tests are not counted: they drive a built bundle in a browser.',
        ]
    )


def main(paths: list[str]) -> None:
    print(to_markdown(dict(read_totals(Path(path)) for path in paths)))


if __name__ == '__main__':
    main(sys.argv[1:])
