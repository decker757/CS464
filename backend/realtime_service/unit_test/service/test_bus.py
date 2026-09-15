"""The Redis subscription, against a real Redis.

Not a fake, for the same reason the other four suites run against Postgres
rather than SQLite: the claims here are about what the bus actually does — that
`listen` yields subscription confirmations as well as messages, that a payload
arrives as text, that a publish to a channel nobody has subscribed to yet is
simply gone. A double would agree with whatever this file assumed.

Start it with `docker compose up -d redis` from the repo root.
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
    """Wait for something to become true, or give up.

    Polled rather than signalled because the thing being waited on is a message
    crossing a network and being dispatched by another task. A fixed sleep long
    enough to be reliable on a loaded CI runner would be long enough to make the
    suite unpleasant locally.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.fixture
async def running_bus(redis_url: str):
    """A subscribed bus, its hub, and a client to publish through.

    Yields only once the subscription is live. Redis pub/sub has no buffering
    whatsoever — a message published before the subscribe lands is not delayed,
    it is discarded — so a test that raced this would fail intermittently and
    look like a bug in the service.
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
    """The most important test in this file.

    `_dispatch` runs inside the `listen` loop. An exception escaping it would
    tear down the subscription for every connected client on this replica
    because one producer sent one bad frame — and the symptom would be prices
    that stop updating everywhere, with nothing in the logs tying it to the
    publish that caused it.
    """
    _, hub, publisher = running_bus
    watcher = Recorder()
    hub.subscribe(watcher, market_id)

    await publisher.publish(PRICE_CHANNEL, payload)
    await publish(publisher, make_event(market_id, 1))

    assert await _settle(lambda: watcher.frames), f"bus died on {description}"
    assert len(watcher.frames) == 1


async def test_an_unreachable_redis_is_retried_rather_than_fatal() -> None:
    """The bus must outlive its bus.

    Every connected client is still holding a socket while Redis is away, so a
    subscriber that gave up and let the task die would turn a blip into a
    service that looks healthy and silently broadcasts nothing, forever. Pointed
    at a port with nothing on it, `run` has to keep going and keep saying it is
    not connected.
    """
    bus = PriceBus(
        # Port 1 is reserved and nothing listens on it, so this fails to connect
        # rather than hanging on a route that might come back.
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
