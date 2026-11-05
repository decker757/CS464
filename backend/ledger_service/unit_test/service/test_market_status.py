"""What `market_status.read_terms` reads for settlement and void. [F-8] #109, ADR 0017.

The trade path's gate is the local copy's (`test_trade_gate.py`,
`test_local_copy.py`, ADR 0030). `read_terms`'s two 404 branches are
`test_settlement.py`'s; what is left here is the statuses settlement compares
its answer against, pinned against market_service's source.
"""

from __future__ import annotations

import pytest

from unit_test.source_pins import BACKEND, enum_member_of


# =========================================================================
# The settlement statuses, pinned against market_service's source
# =========================================================================
@pytest.mark.parametrize(
    ("ours", "member"), [("_APPROVED", "APPROVED"), ("_SETTLED", "SETTLED")]
)
def test_the_settlement_statuses_match_the_ones_market_service_puts_on_the_wire(
    ours: str, member: str
) -> None:
    """Settlement's copies of `"approved"` and `"settled"`, pinned against
    their source as `test_local_copy.py` pins `"open"`. The fake upstream in
    `settlement_fixtures.py` types the same literal, so a rename upstream would
    refuse every settlement as market_not_approved with every settlement test
    still green."""
    from service import settlement  # noqa: PLC0415

    theirs = enum_member_of(
        BACKEND / "market_service" / "model" / "entities.py",
        "MarketStatus",
        member,
    )

    assert getattr(settlement, ours) == theirs, (
        f"settlement reads {getattr(settlement, ours)!r} and market_service "
        f"publishes {theirs!r}; every settlement would be refused"
    )
