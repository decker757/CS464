"""Market ids market_service recently answered 404 for. #115, D-053.

Pure and in-process: an ordered map from id to expiry, bounded, on a clock the
caller can replace. `service/market_terms.py` holds the one instance and says
how long and how many.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Callable


class NotFoundCache:
    """Remembers a 404 for `ttl_seconds`, at most `max_entries` at once.

    Oldest first out, both by expiry and by eviction: every entry gets the
    same window, so insertion order is expiry order. Not safe across threads;
    the ledger runs it on one event loop and nothing here awaits.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._expiry_by_id: OrderedDict[uuid.UUID, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._expiry_by_id)

    def _drop_expired(self, now: float) -> None:
        while self._expiry_by_id:
            oldest_id, expires_at = next(iter(self._expiry_by_id.items()))
            if expires_at > now:
                return
            del self._expiry_by_id[oldest_id]

    def remember(self, market_id: uuid.UUID) -> None:
        """Remember a 404 for this id, restarting its window if it had one.
        Forgets the oldest id when that would exceed the bound."""
        now = self._clock()
        self._drop_expired(now)

        # Popped first, so a repeat moves to the newest end: it expires last
        # and is evicted last.
        self._expiry_by_id.pop(market_id, None)
        self._expiry_by_id[market_id] = now + self._ttl_seconds

        while len(self._expiry_by_id) > self._max_entries:
            self._expiry_by_id.popitem(last=False)

    def is_remembered(self, market_id: uuid.UUID) -> bool:
        """True if this id answered 404 within the window. Changes nothing."""
        expires_at = self._expiry_by_id.get(market_id)
        return expires_at is not None and expires_at > self._clock()
