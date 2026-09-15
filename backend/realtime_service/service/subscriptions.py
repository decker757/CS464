"""Who is watching which market. [F-2] #42

The fan-out, and nothing else. This module holds no WebSocket type and imports
nothing from `controller`: a subscriber here is anything with an `enqueue`
method, which is what lets the whole of the routing logic be tested without a
socket, a browser or an event loop full of network.

The concrete subscriber — the one that owns a real WebSocket, a send queue and
a pump — is `controller/connection.py`, because a send queue is transport. The
rule that a market's event reaches exactly the connections watching that market
is a business rule, and it lives here.

**Not shared between processes, and does not need to be.** Each replica holds
its own connections and its own map. Redis delivers every event to every
replica, and each forwards to whoever it is holding. Nothing here has to know
that another replica exists, which is the property that makes this service
horizontally scalable without any coordination at all.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from core.config import get_settings
from core.errors import TooManySubscriptions


class Subscriber(Protocol):
    """Anything that can be handed a frame.

    A Protocol rather than a base class so that the test double is a five-line
    class that collects dicts, rather than something that has to inherit a
    WebSocket's worth of machinery to be allowed into a set.
    """

    def enqueue(self, frame: dict[str, Any]) -> None:
        """Take this frame, or arrange to be dropped.

        Must not block and must not raise. A fan-out that could raise would let
        one broken connection interrupt delivery to everyone after it in the
        loop, and the ordering of a `set` is not something anybody should be
        able to be unlucky about.
        """


def _discard[K, V](index: dict[K, set[V]], key: K, value: V) -> None:
    """Remove one value from one entry of a set index, pruning an emptied entry.

    Dropped rather than left empty because both of this hub's maps are
    long-lived and only ever gain keys otherwise. Markets accumulate for the
    life of the platform and connections come and go, so an index that never
    pruned would be a slow leak in the one process meant to stay up for days —
    and it would quietly make `len(index)` mean something other than what its
    callers read it as.
    """
    members = index.get(key)
    if members is None:
        return

    members.discard(value)
    if not members:
        del index[key]


class Hub:
    """The connections, indexed both ways.

    `_by_market` answers "who wants this event", which is every broadcast.
    `_by_subscriber` answers "what was this connection watching", which is
    every disconnect. Keeping both is a few bytes per subscription and it is
    what stops a disconnect from being a scan of every market on the platform —
    connections drop constantly, and a price feed that got slower the more
    markets existed would get slower for a reason unrelated to load.
    """

    def __init__(self) -> None:
        self._by_market: dict[uuid.UUID, set[Subscriber]] = {}
        self._by_subscriber: dict[Subscriber, set[uuid.UUID]] = {}

    # -- membership -------------------------------------------------------

    def subscribe(self, subscriber: Subscriber, market_id: uuid.UUID) -> None:
        """Idempotent. Subscribing twice is one subscription.

        A client that resubscribes after a reconnect it did not notice should
        not receive every price twice, and sets make that true for free rather
        than by the client being careful.

        Raises `TooManySubscriptions` past the per-connection ceiling. The check
        lives here rather than in the route because it is a rule about this
        hub's own state, and a rule enforced by its only caller is a rule the
        second caller will not enforce — a bulk-subscribe command or an admin
        tool would silently bypass a cap held in the controller. It also means
        the ceiling is tested without a socket, which is where the backend
        conventions put a business rule.

        The already-subscribed case is checked first, so a client re-sending a
        subscription it already holds is never refused for a ceiling it is not
        pushing against. The limit counts markets, not commands.
        """
        if (
            not self.is_subscribed(subscriber, market_id)
            and self.subscription_count(subscriber)
            >= get_settings().max_subscriptions_per_connection
        ):
            raise TooManySubscriptions

        self._by_market.setdefault(market_id, set()).add(subscriber)
        self._by_subscriber.setdefault(subscriber, set()).add(market_id)

    def unsubscribe(self, subscriber: Subscriber, market_id: uuid.UUID) -> None:
        """Also idempotent, and silent about a subscription that was not there.

        The client's view of what it is watching and this one can differ by a
        frame in flight, and an error for unsubscribing from something already
        gone would be an error for a race the client cannot avoid.

        Both maps go through the same helper. Writing the cleanup out twice by
        hand is exactly how they came to disagree: the market side pruned its
        empty sets and the subscriber side did not, so a client that
        unsubscribed from its last market and stayed connected was still
        counted by `connection_count`.
        """
        _discard(self._by_market, market_id, subscriber)
        _discard(self._by_subscriber, subscriber, market_id)

    def forget(self, subscriber: Subscriber) -> None:
        """Remove a subscriber from every market it was watching.

        Called on disconnect, and it must be safe to call for a connection that
        never subscribed to anything — which is every connection that was closed
        for an expired token before it said a word.

        No separate cleanup of `_by_subscriber` afterwards: `_discard` drops the
        key with the last market, and `subscribe` is the only thing that creates
        one, so an entry holding an empty set cannot exist to be left behind.
        """
        for market_id in list(self._by_subscriber.get(subscriber, ())):
            self.unsubscribe(subscriber, market_id)

    # -- delivery ---------------------------------------------------------

    def broadcast(self, market_id: uuid.UUID, frame: dict[str, Any]) -> int:
        """Hand this frame to everyone watching this market. Returns how many.

        Iterates a copy of the set, because a subscriber that fails while being
        enqueued to arranges its own removal, and mutating a set mid-iteration
        raises. The copy is the size of the audience for one market, not of the
        platform.
        """
        watchers = self._by_market.get(market_id)
        if not watchers:
            return 0

        for subscriber in list(watchers):
            subscriber.enqueue(frame)
        return len(watchers)

    # -- introspection ----------------------------------------------------

    def subscriber_count(self, market_id: uuid.UUID) -> int:
        return len(self._by_market.get(market_id, ()))

    def is_subscribed(self, subscriber: Subscriber, market_id: uuid.UUID) -> bool:
        """Whether this connection is already watching this market.

        Asked before the per-connection limit is enforced, so that a client
        re-sending a subscribe it already holds is not refused for being at a
        ceiling it is not actually pushing against. Subscribing is idempotent;
        the limit counts markets, not commands.
        """
        return market_id in self._by_subscriber.get(subscriber, ())

    def subscription_count(self, subscriber: Subscriber) -> int:
        """How many markets this connection is watching.

        Read by the route to enforce `MAX_SUBSCRIPTIONS_PER_CONNECTION`. Asked
        of the hub rather than tracked on the connection so that there is one
        answer to the question rather than two that can disagree.
        """
        return len(self._by_subscriber.get(subscriber, ()))

    @property
    def connection_count(self) -> int:
        """Subscribers holding at least one subscription.

        Not the number of open sockets: a connection that has authenticated and
        not yet subscribed is unknown to the hub, which is the correct place for
        it to be.
        """
        return len(self._by_subscriber)


_hub: Hub | None = None


def get_hub() -> Hub:
    """The process-wide hub.

    A module-level singleton with an accessor, the same shape as
    `core/database.py`'s `get_engine` in the other services, and reset the same
    way between tests.
    """
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def reset_hub() -> None:
    """Drop the hub. For tests, and for nothing else.

    The production lifespan never calls this: a process that has lost its hub
    has lost every connection's routing while the sockets are still open.
    """
    global _hub
    _hub = None
