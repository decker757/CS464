"""Only the trade path writes `ledger.positions`. [T-3] #23

**A tripwire, not a test of behaviour.** Nothing here runs a trade. It reads
the source of every module in this service and fails when a module other
than `service/trading.py` writes a `Position` — constructs one, hands the
entity to `insert`, `update` or `delete`, assigns to one of its columns, or
names the table in a raw `INSERT`, `UPDATE` or `DELETE`.

It exists because of the D-NEW entry "The holdings check is read under the
book lock, and the position takes no lock of its own". The sell's holding
check reads the position with no `with_for_update()`, and that is safe only
because every writer of positions takes the market's book row lock first.
The trade path does. A second writer that skipped the book lock —
settlement, [3.4] #12, is the likely one — would read the same share count
as a concurrent sell, and one would overwrite the other.

When this fails, do not just add the module to `_KNOWN_WRITERS`. Read that
entry first. Either the new writer takes the book lock before it reads or
writes a position, and says so beside its line below, or the entry's
reversal trigger has fired and the position read gets its own `FOR UPDATE`,
in both writers.

The scan is deliberately coarse: a module that imports the `Position` entity
and assigns to any attribute named like one of its columns counts as a
writer. A false alarm costs one look; a missed writer costs a lost update.
"""

from __future__ import annotations

from model.entities import Position
from unit_test.writer_scan import writers_of

# Every module allowed to write a position, and how it holds the row still.
_KNOWN_WRITERS = {
    "service/trading.py": "takes the book row's FOR UPDATE before it reads a position",
}


def test_the_trade_path_is_the_only_writer_of_positions() -> None:
    """Also fails if the scan stops seeing `service/trading.py`, so a scan
    that has gone blind cannot pass by finding nothing anywhere."""
    writers = writers_of(Position)

    assert set(writers) == set(_KNOWN_WRITERS), (
        "the modules that write ledger.positions changed. A new writer must "
        "take the book row lock before it touches a position, or the position "
        "read gets its own FOR UPDATE — see the D-NEW entry \"The holdings "
        "check is read under the book lock, and the position takes no lock of "
        "its own\".\n"
        + "\n".join(f"{name}: {found}" for name, found in writers.items())
    )
