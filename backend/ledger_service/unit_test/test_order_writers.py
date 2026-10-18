"""Every writer of `ledger.orders` is known. [T-6] #26

**A tripwire, not a test of behaviour.** Nothing here places an order. It
reads the source of every module in this service and fails when a module not
listed below writes an `Order` — constructs one, hands the entity to
`insert`, `update` or `delete`, assigns to one of its columns, or names the
table in a raw `INSERT`, `UPDATE` or `DELETE`.

It exists because of the 2026-10-17 amendment to the D-NEW entry "The
holdings check is read under the book lock, and the position takes no lock of
its own": every writer of an order row, the placement, the cascade and the
cancel, takes the market's book row lock first, so the book lock holds the
rows still and the order read takes no lock of its own. ADR 0024's "Price
priority, then time" leans on the same lock: `seq` is assigned when the row
is inserted under it, so the sequence is the lock order. A writer that skipped
the book lock would break both.

When this fails, do not just add the module to `_KNOWN_WRITERS`. Read that
amendment first. Either the new writer takes the book lock before it reads or
writes an order row, and says so beside its line below, or the amendment's
reversal trigger has fired and the order read gets its own `FOR UPDATE`.

The scan is `writer_scan.py`'s, shared with `test_position_writers.py`, and it
is coarse: a false alarm costs one look; a missed writer costs a lost update.
"""

from __future__ import annotations

from model.entities import Order
from unit_test.writer_scan import writers_of

# Every module allowed to write an order row, and how it holds the row still.
# The cancel adds its own, with the lock it takes.
_KNOWN_WRITERS = {
    "service/orders.py": "takes the book row's FOR UPDATE before it inserts an order",
    "service/trading.py": (
        "the cascade rolls a fill into its order under the book row's FOR UPDATE, "
        "which execute and the placement take first"
    ),
}


def test_the_known_writers_are_the_only_writers_of_orders() -> None:
    writers = writers_of(Order)

    assert set(writers) == set(_KNOWN_WRITERS), (
        "the modules that write ledger.orders changed. A new writer must take "
        "the book row lock before it touches an order row — see the 2026-10-17 "
        'amendment to the D-NEW entry "The holdings check is read under the '
        'book lock, and the position takes no lock of its own".\n'
        + "\n".join(f"{name}: {found}" for name, found in writers.items())
    )
