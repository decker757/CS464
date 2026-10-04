"""The memory of market ids market_service answered 404 for. #115

DECISIONS.md: "A market_service 404 is remembered for ten seconds, per process".

Pure, no database: the clock is passed in, so expiry is tested by moving it
rather than by sleeping. `test_market_terms.py` tests the cache as `fetch`
uses it.
"""

from __future__ import annotations

import uuid

from core.not_found_cache import NotFoundCache
from unit_test.conftest import ManualClock


def test_an_id_is_remembered_until_its_window_passes() -> None:
    """A draft is a 404 today and a published market tomorrow, so the memory
    has to end. Remembered just before the window closes, gone at it."""
    clock = ManualClock()
    cache = NotFoundCache(ttl_seconds=10.0, max_entries=100, clock=clock)
    market_id = uuid.uuid4()

    cache.remember(market_id)

    clock.now = 9.999
    assert cache.is_remembered(market_id)
    clock.now = 10.0
    assert not cache.is_remembered(market_id)


def test_the_bound_holds_and_the_oldest_goes_first() -> None:
    """Looping random ids must not grow the memory without limit. Past the
    bound the oldest is forgotten, which costs one more call, never a wrong
    answer."""
    clock = ManualClock()
    cache = NotFoundCache(ttl_seconds=10.0, max_entries=3, clock=clock)
    ids = [uuid.uuid4() for _ in range(4)]

    for market_id in ids:
        cache.remember(market_id)
        clock.now += 0.001

    assert len(cache) == 3
    assert not cache.is_remembered(ids[0])
    assert all(cache.is_remembered(market_id) for market_id in ids[1:])


def test_remembering_an_id_again_restarts_its_window_and_its_place() -> None:
    """A second 404 is a fresher fact than the first: it waits its full window
    again and is evicted after ids remembered before it."""
    clock = ManualClock()
    cache = NotFoundCache(ttl_seconds=10.0, max_entries=2, clock=clock)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    cache.remember(first)
    clock.now = 5.0
    cache.remember(second)
    cache.remember(first)
    cache.remember(third)

    assert cache.is_remembered(first), "the refreshed id was evicted as oldest"
    assert not cache.is_remembered(second)

    clock.now = 14.0
    assert cache.is_remembered(first), "the refreshed id kept its old expiry"
