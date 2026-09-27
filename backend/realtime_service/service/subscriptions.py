"""Who is watching which market: the fan-out, and nothing else. [F-2] #42

Knows no WebSocket: a subscriber is anything with `enqueue`, so the routing
rules are tested without a socket. The real subscriber is
`controller/connection.py`. Each replica keeps its own map; replicas coordinate
about nothing. ADR 0010.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from core.config import get_settings
from core.errors import TooManySubscriptions


class Subscriber(Protocol):
    """Anything that can be handed a frame. A Protocol, so a test double needs no base class."""

    def enqueue(self, frame: dict[str, Any]) -> None:
        """Take this frame, or arrange to be dropped.

        Must not block and must not raise, or one broken connection would
        interrupt delivery to everyone after it in the loop.
        """


def _discard[K, V](index: dict[K, set[V]], key: K, value: V) -> None:
    """Remove one value from one entry of a set index, pruning an emptied entry.

    Both of the hub's maps live as long as the process. Without pruning they
    only grow, and `len(index)` stops meaning "entries in use".
    """
    members = index.get(key)
    if members is None:
        return

    members.discard(value)
    if not members:
        del index[key]


class Hub:
    """The subscriptions, indexed both ways.

    `_by_market` serves every broadcast; `_by_subscriber` serves every
    disconnect, so dropping a connection is not a scan of every market.
    """

    def __init__(self) -> None:
        self._by_market: dict[uuid.UUID, set[Subscriber]] = {}
        self._by_subscriber: dict[Subscriber, set[uuid.UUID]] = {}

    # -- introspection ----------------------------------------------------

    def subscriber_count(self, market_id: uuid.UUID) -> int:
        """How many subscribers this market has."""
        return len(self._by_market.get(market_id, ()))

    def is_subscribed(self, subscriber: Subscriber, market_id: uuid.UUID) -> bool:
        """Whether this connection is already watching this market."""
        return market_id in self._by_subscriber.get(subscriber, ())

    def subscription_count(self, subscriber: Subscriber) -> int:
        """How many markets this connection is watching.

        Asked of the hub rather than counted on the connection, so there is one answer.
        """
        return len(self._by_subscriber.get(subscriber, ()))

    @property
    def connection_count(self) -> int:
        """Subscribers holding at least one subscription; not the open sockets."""
        return len(self._by_subscriber)

    # -- membership -------------------------------------------------------

    def subscribe(self, subscriber: Subscriber, market_id: uuid.UUID) -> None:
        """Subscribe, idempotently. Raises `TooManySubscriptions` past the ceiling.

        The ceiling is enforced here, not in the route, so no second caller can
        bypass it. A market already held is never refused: the limit counts
        markets, not commands.
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
        """Idempotent, and silent about a subscription that was not there.

        The client's view can lag this one by a frame in flight. Both maps must
        prune through `_discard`; cleanup written by hand for one of them lets
        the two disagree and `connection_count` over-count.
        """
        _discard(self._by_market, market_id, subscriber)
        _discard(self._by_subscriber, subscriber, market_id)

    def forget(self, subscriber: Subscriber) -> None:
        """Remove a subscriber from every market. Safe for one that never subscribed."""
        for market_id in list(self._by_subscriber.get(subscriber, ())):
            self.unsubscribe(subscriber, market_id)

    # -- delivery ---------------------------------------------------------

    def broadcast(self, market_id: uuid.UUID, frame: dict[str, Any]) -> int:
        """Hand this frame to everyone watching this market. Returns how many.

        Iterates a copy, defensively: a failing subscriber is removed later
        by `forget`, but nothing here should break if one is removed mid-loop.
        """
        watchers = self._by_market.get(market_id)
        if not watchers:
            return 0

        for subscriber in list(watchers):
            subscriber.enqueue(frame)
        return len(watchers)


_hub: Hub | None = None


def get_hub() -> Hub:
    """The process-wide hub."""
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def reset_hub() -> None:
    """Drop the hub. For tests only: in production it would orphan every open socket."""
    global _hub
    _hub = None
