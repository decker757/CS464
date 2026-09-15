"""The fan-out: who receives which market's events.

Driven entirely through the `Subscriber` protocol, with no socket anywhere —
which is the point of the protocol. Every rule here is a business rule and it is
tested without the transport, exactly as the backend conventions require.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.config import get_settings
from core.errors import TooManySubscriptions
from service.subscriptions import Hub
from unit_test.conftest import Recorder


def test_a_subscriber_receives_its_market(market_id: uuid.UUID) -> None:
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    delivered = hub.broadcast(market_id, {"type": "price"})

    assert delivered == 1
    assert watcher.frames == [{"type": "price"}]


def test_a_subscriber_does_not_receive_another_market(
    market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    """The whole point of the index. A price feed that leaked across markets
    would show a trader one market's price on another's page."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    hub.broadcast(other_market_id, {"type": "price"})

    assert watcher.frames == []


def test_every_watcher_of_a_market_receives_it(market_id: uuid.UUID) -> None:
    hub = Hub()
    watchers = [Recorder() for _ in range(3)]
    for watcher in watchers:
        hub.subscribe(watcher, market_id)

    assert hub.broadcast(market_id, {"type": "price"}) == 3
    assert all(len(watcher.frames) == 1 for watcher in watchers)


def test_subscribing_twice_delivers_once(market_id: uuid.UUID) -> None:
    """A client that resubscribed after a reconnect it did not notice should
    not receive every price twice."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)
    hub.subscribe(watcher, market_id)

    hub.broadcast(market_id, {"type": "price"})

    assert len(watcher.frames) == 1
    assert hub.subscription_count(watcher) == 1


def test_unsubscribing_stops_delivery(market_id: uuid.UUID) -> None:
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)
    hub.unsubscribe(watcher, market_id)

    assert hub.broadcast(market_id, {"type": "price"}) == 0
    assert watcher.frames == []


def test_unsubscribing_from_something_unsubscribed_is_silent(
    market_id: uuid.UUID,
) -> None:
    """The client's view and this one can differ by a frame in flight, and an
    error for a race the client cannot avoid is not a useful error."""
    hub = Hub()
    watcher = Recorder()

    hub.unsubscribe(watcher, market_id)  # must not raise


def test_forget_removes_a_subscriber_from_every_market(
    market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    """Called on every disconnect. A connection left in the hub is a connection
    the next broadcast tries to write to."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)
    hub.subscribe(watcher, other_market_id)

    hub.forget(watcher)

    assert hub.broadcast(market_id, {"type": "price"}) == 0
    assert hub.broadcast(other_market_id, {"type": "price"}) == 0
    assert hub.connection_count == 0


def test_forget_is_safe_for_a_subscriber_that_never_subscribed() -> None:
    """Every connection closed for an expired token before it said a word."""
    Hub().forget(Recorder())  # must not raise


def test_forgetting_one_subscriber_leaves_the_others(market_id: uuid.UUID) -> None:
    hub = Hub()
    leaving, staying = Recorder(), Recorder()
    hub.subscribe(leaving, market_id)
    hub.subscribe(staying, market_id)

    hub.forget(leaving)

    assert hub.broadcast(market_id, {"type": "price"}) == 1
    assert len(staying.frames) == 1


def test_broadcasting_to_a_market_nobody_watches_is_free(
    market_id: uuid.UUID,
) -> None:
    assert Hub().broadcast(market_id, {"type": "price"}) == 0


def test_the_market_index_does_not_leak_keys(market_id: uuid.UUID) -> None:
    """Markets accumulate for the life of the platform and connections come and
    go. A map that only ever gained keys would be a slow leak in the one
    process meant to stay up for days."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)
    hub.unsubscribe(watcher, market_id)

    assert hub.subscriber_count(market_id) == 0
    assert hub._by_market == {}


def test_a_subscriber_that_removes_itself_mid_broadcast_does_not_break_it(
    market_id: uuid.UUID,
) -> None:
    """`Connection.enqueue` arranges its own removal when it overflows, and that
    happens inside this loop. Iterating the set directly would raise
    "Set changed size during iteration" and silently cut delivery short for
    everybody after it."""
    hub = Hub()

    class SelfRemoving:
        def __init__(self) -> None:
            self.frames: list[dict[str, Any]] = []

        def enqueue(self, frame: dict[str, Any]) -> None:
            self.frames.append(frame)
            hub.forget(self)

    quitter, survivor = SelfRemoving(), Recorder()
    hub.subscribe(quitter, market_id)
    hub.subscribe(survivor, market_id)

    hub.broadcast(market_id, {"type": "price"})

    assert len(quitter.frames) == 1
    assert len(survivor.frames) == 1


def test_is_subscribed_reports_membership(
    market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    """Read before the per-connection limit is enforced, so that re-sending a
    subscription already held is not refused for a ceiling it is not pushing
    against."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    assert hub.is_subscribed(watcher, market_id) is True
    assert hub.is_subscribed(watcher, other_market_id) is False


def test_connection_count_ignores_a_subscriber_watching_nothing(
    market_id: uuid.UUID,
) -> None:
    """Not the number of open sockets. A connection that has authenticated and
    not yet subscribed is unknown to the hub, which is where it belongs."""
    hub = Hub()
    watcher = Recorder()

    assert hub.connection_count == 0

    hub.subscribe(watcher, market_id)
    assert hub.connection_count == 1


def test_unsubscribing_from_the_last_market_leaves_no_connection_behind(
    market_id: uuid.UUID,
) -> None:
    """The half of the bookkeeping that `forget` does not cover.

    `connection_count` says "subscribers holding at least one subscription", and
    a client that unsubscribes from its last market while staying connected
    holds none. This failed while the two indexes pruned differently: the market
    side dropped its emptied set and the subscriber side left one sitting under
    the key, so the count stayed at one until the socket actually closed.
    """
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    hub.unsubscribe(watcher, market_id)

    assert hub.connection_count == 0
    assert hub.subscription_count(watcher) == 0
    assert hub._by_subscriber == {}


def test_unsubscribing_from_one_of_two_markets_keeps_the_connection(
    market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    """The other side of the rule above: pruning is per entry, not per
    subscriber, so a client narrowing what it watches is still a connection."""
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)
    hub.subscribe(watcher, other_market_id)

    hub.unsubscribe(watcher, market_id)

    assert hub.connection_count == 1
    assert hub.subscription_count(watcher) == 1
    assert hub.is_subscribed(watcher, other_market_id)


def test_a_connection_may_not_watch_unlimited_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that subscribes in a loop is the cheapest denial of service
    available against a server whose whole job is holding connections open.

    Tested here rather than through a socket because it is a rule about the
    hub's own state. It used to live in the route, which meant a second caller
    — a bulk-subscribe command, an admin tool — would have bypassed it silently.
    """
    monkeypatch.setattr(get_settings(), "max_subscriptions_per_connection", 2)
    hub = Hub()
    watcher = Recorder()

    hub.subscribe(watcher, uuid.uuid4())
    hub.subscribe(watcher, uuid.uuid4())

    with pytest.raises(TooManySubscriptions):
        hub.subscribe(watcher, uuid.uuid4())

    assert hub.subscription_count(watcher) == 2


def test_resubscribing_at_the_limit_is_allowed(
    monkeypatch: pytest.MonkeyPatch, market_id: uuid.UUID
) -> None:
    """Subscribing is idempotent and the limit counts markets, not commands, so
    a client re-sending a subscription it already holds is not pushing against
    the ceiling."""
    monkeypatch.setattr(get_settings(), "max_subscriptions_per_connection", 1)
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    hub.subscribe(watcher, market_id)  # must not raise

    assert hub.subscription_count(watcher) == 1


def test_a_refused_subscription_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, market_id: uuid.UUID
) -> None:
    """Refused rather than partially applied. A connection that was told no
    must not find itself in the market index anyway, or the next broadcast
    would write to a subscription the client does not believe it has."""
    monkeypatch.setattr(get_settings(), "max_subscriptions_per_connection", 1)
    hub = Hub()
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    refused = uuid.uuid4()
    with pytest.raises(TooManySubscriptions):
        hub.subscribe(watcher, refused)

    assert hub.subscriber_count(refused) == 0
    assert not hub.is_subscribed(watcher, refused)
