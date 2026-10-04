"""The Redis subscription, against a real Redis (`docker compose up -d redis`).

Not a fake: the claims here are about what Redis actually does, and a double
would agree with whatever this file assumed.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import redis.asyncio as redis

from service.bus import PRICE_CHANNEL, PriceBus, publish
from service.ordering import VersionGate
from service.subscriptions import Hub
from unit_test.conftest import REDIS_UNREACHABLE, Recorder, make_event


async def _settle(predicate, *, timeout: float = 5.0) -> bool:
    """Poll until something becomes true, or give up after `timeout` seconds."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.fixture
async def running_bus(redis_url: str):
    """A subscribed bus, its hub, and a client to publish through.

    Yields only once the subscription is live: Redis discards a message
    published before the subscribe lands.
    """
    hub, gate = Hub(), VersionGate()
    bus = PriceBus(redis_url=redis_url, hub=hub, gate=gate)
    task = asyncio.create_task(bus.run())

    if not await _settle(lambda: bus.connected):
        task.cancel()
        pytest.fail(REDIS_UNREACHABLE)

    publisher = redis.from_url(redis_url, decode_responses=True)
    try:
        yield bus, hub, publisher
    finally:
        await publisher.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_published_event_reaches_a_subscriber(
    running_bus, market_id: uuid.UUID
) -> None:
    """The whole path: publish, decode, gate, fan out."""
    _, hub, publisher = running_bus
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    await publish(publisher, make_event(market_id, 1))

    assert await _settle(lambda: watcher.frames)
    assert watcher.frames[0]["type"] == "price"
    assert watcher.frames[0]["market_id"] == str(market_id)
    assert watcher.frames[0]["state_version"] == 1


async def test_an_event_for_an_unwatched_market_goes_nowhere(
    running_bus, market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    _, hub, publisher = running_bus
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    await publish(publisher, make_event(other_market_id, 1))
    await publish(publisher, make_event(market_id, 1))

    assert await _settle(lambda: watcher.frames)
    assert len(watcher.frames) == 1
    assert watcher.frames[0]["market_id"] == str(market_id)


async def test_a_stale_event_is_not_delivered(
    running_bus, market_id: uuid.UUID
) -> None:
    """The gate, in place. Two events, newest first."""
    _, hub, publisher = running_bus
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    await publish(publisher, make_event(market_id, 9))
    assert await _settle(lambda: watcher.frames)

    await publish(publisher, make_event(market_id, 8))
    await publish(publisher, make_event(market_id, 10))

    assert await _settle(lambda: len(watcher.frames) == 2)
    assert [f["state_version"] for f in watcher.frames] == [9, 10]


@pytest.mark.parametrize(
    ("description", "payload"),
    [
        ("not json at all", "}{"),
        ("json but not an object", '"hello"'),
        ("missing state_version", json.dumps({"market_id": str(uuid.uuid4())})),
        (
            "an unknown field",
            json.dumps(
                {
                    "market_id": str(uuid.uuid4()),
                    "state_version": 1,
                    "prices": [],
                    "occurred_at": "2026-09-15T00:00:00+00:00",
                    "surprise": True,
                }
            ),
        ),
    ],
)
async def test_a_malformed_message_does_not_stop_the_bus(
    running_bus, market_id: uuid.UUID, description: str, payload: str
) -> None:
    """`_dispatch` runs inside `listen`; an exception escaping it would stop
    prices for every client on this replica over one bad frame."""
    _, hub, publisher = running_bus
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    await publisher.publish(PRICE_CHANNEL, payload)
    await publish(publisher, make_event(market_id, 1))

    assert await _settle(lambda: watcher.frames), f"bus died on {description}"
    assert len(watcher.frames) == 1


async def test_an_unreachable_redis_is_retried_rather_than_fatal() -> None:
    """A bus that gave up would leave a healthy-looking service relaying
    nothing, forever. `run` must keep going and keep reporting disconnected."""
    bus = PriceBus(
        # Nothing listens on port 1, so this fails fast rather than hanging.
        redis_url="redis://localhost:1/0",
        hub=Hub(),
        gate=VersionGate(),
    )
    task = asyncio.create_task(bus.run())

    await asyncio.sleep(0.2)
    still_running = not task.done()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert still_running, "the bus gave up on an unreachable Redis"
    assert bus.connected is False
