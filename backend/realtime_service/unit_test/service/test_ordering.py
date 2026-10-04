"""The staleness guard. [X-4] #37's fourth acceptance criterion, server side."""

from __future__ import annotations

import uuid

from service.ordering import VersionGate


def test_the_first_event_for_a_market_is_accepted(market_id: uuid.UUID) -> None:
    """Whatever version it carries. This replica may have started after a
    thousand trades, and refusing to forward anything until it had seen a lower
    number would mean a restarted replica serves nobody."""
    gate = VersionGate()

    assert gate.accept(market_id, 907) is True


def test_a_newer_version_is_accepted(market_id: uuid.UUID) -> None:
    gate = VersionGate()
    gate.accept(market_id, 1)

    assert gate.accept(market_id, 2) is True


def test_the_same_version_twice_is_dropped(market_id: uuid.UUID) -> None:
    """The duplicate a producer creates by retrying after a timeout.

    Equal is not "no change": a genuine change always advances the counter,
    because the counter is incremented in the same transaction as the change.
    An equal version is therefore always the same event arriving twice.
    """
    gate = VersionGate()
    gate.accept(market_id, 5)

    assert gate.accept(market_id, 5) is False


def test_an_older_version_is_dropped(market_id: uuid.UUID) -> None:
    """The one the ticket is actually about: "stale events cannot overwrite
    newer prices". Redis promises nothing about ordering across a reconnect."""
    gate = VersionGate()
    gate.accept(market_id, 9)

    assert gate.accept(market_id, 8) is False


def test_a_dropped_event_does_not_move_the_high_water_mark(
    market_id: uuid.UUID,
) -> None:
    """Otherwise one late event would lower the bar and let every event
    between it and the newest through a second time."""
    gate = VersionGate()
    gate.accept(market_id, 9)
    gate.accept(market_id, 3)

    assert gate.latest(market_id) == 9
    assert gate.accept(market_id, 9) is False
    assert gate.accept(market_id, 10) is True


def test_markets_are_gated_independently(
    market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    """Version numbers are per market and two markets share no sequence. A
    global high-water mark would silence every market with a lower counter than
    the busiest one."""
    gate = VersionGate()
    gate.accept(market_id, 100)

    assert gate.accept(other_market_id, 1) is True


def test_latest_is_none_for_a_market_never_seen(market_id: uuid.UUID) -> None:
    assert VersionGate().latest(market_id) is None
