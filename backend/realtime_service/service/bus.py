"""The pub/sub side. [F-2] #42

One Redis channel carries every market's price events. This service subscribes
once at startup, and for the life of the process it decodes what arrives, drops
what is stale, and hands the rest to the hub.

**Why one channel rather than one per market.** Redis would happily filter for
us with `market.price.<id>`, and at a scale this project will not reach that is
the more efficient shape. It costs a subscribe and an unsubscribe round trip
every time a market's last watcher disconnects, plus the reference counting to
know when that happened, plus the races between a client subscribing and the
Redis subscription landing. One channel and a dict lookup has none of that, and
`Hub.broadcast` is an O(1) miss for a market nobody is watching. Move to
channel-per-market when a replica is measurably spending its time discarding
other people's events, and not before.

**Why the producer is not in this file.** This service never publishes a price;
it has no `q`, no `b` and no opinion about what anything costs. `publish` below
exists as the executable half of the contract — the suite and
`scripts/publish_test_price.py` drive the service through it, and [T-2] #22
copies its four lines rather than importing them. That was once forced — Docker
build contexts could not reach across service directories — and since [F-6] #76
it is not: `backend/shared/` is importable everywhere. Four lines of
`redis.publish` are still below the bar that package sets for itself, so the
copy stands on its own merits now.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis.asyncio as redis
from pydantic import ValidationError

from model.schemas import PriceEvent
from service.ordering import VersionGate
from service.subscriptions import Hub

logger = logging.getLogger(__name__)

# The channel [T-2] #22 publishes to and this service subscribes to. Changing
# this string is a contract change that breaks the producer silently — the
# publish still succeeds, into a channel nobody is listening on — so it is
# named here once and documented in `docs/api/realtime-service.md`.
PRICE_CHANNEL = "market.price"

# Reconnect backoff. Redis going away is not an error worth crashing the
# process over: every connected client is still holding a socket, and dropping
# all of them because the bus blinked would turn a blip into a reconnect storm
# against a service whose job is holding connections open.
_BACKOFF_INITIAL_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 10.0


async def publish(client: redis.Redis, event: PriceEvent) -> None:
    """Put one price event on the channel.

    The producer's half of the contract, and the only part of it that is code
    in this repository. [T-2] #22 does exactly this, from its own service,
    **after** the trade's transaction has committed — publishing before the
    commit would announce a price that a rollback then un-made.

    That ordering leaves a window: a crash between the commit and this call
    loses the broadcast. It is not closed here and it is not closed anywhere,
    because the client's reconnect-and-snapshot path already recovers from it
    and an outbox with a relay is a large amount of machinery to avoid a
    momentarily stale price on a screen that is about to be refreshed anyway.
    ADR 0010 records the trade; ADR 0006 made the same call for the audit log
    from the other direction.
    """
    await client.publish(PRICE_CHANNEL, event.model_dump_json())


class PriceBus:
    """Subscribes to the channel and drives the hub until it is cancelled."""

    def __init__(self, *, redis_url: str, hub: Hub, gate: VersionGate) -> None:
        self._redis_url = redis_url
        self._hub = hub
        self._gate = gate
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the subscription is currently live.

        Reported by `/health` as a separate field rather than folded into
        `status`. A relay that cannot reach its bus is not healthy in any
        meaningful sense, but failing the probe would have the orchestrator
        restart the container and drop every socket it is holding — which is
        the one thing that definitely makes a Redis blip worse. So the probe
        stays green and says what is wrong.
        """
        return self._connected

    async def run(self) -> None:
        """Consume the channel forever, reconnecting as needed.

        Returns only on cancellation. Every other failure is retried, because
        there is no failure mode here that is improved by the process exiting:
        every client is still holding a socket, and dropping all of them because
        the bus blinked turns a blip into a reconnect storm.
        """
        backoff = _BACKOFF_INITIAL_SECONDS

        while True:
            try:
                subscribed = await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception:
                subscribed = False
                logger.exception("price bus disconnected; retrying in %.1fs", backoff)

            # The wait happens out here, after `_consume` has closed its client,
            # rather than inside the failure branch. A `finally` in there would
            # run only once the branch it belongs to had finished, so a dead
            # connection would be held open for the whole backoff — up to ten
            # seconds of socket kept for a client that is never read again.
            #
            # A connection that actually came up resets the backoff, so a long
            # healthy subscription that drops reconnects promptly and only a
            # genuinely unreachable Redis is backed off from.
            if subscribed:
                backoff = _BACKOFF_INITIAL_SECONDS

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)

    async def _consume(self) -> bool:
        """One connection, from subscribe until it ends.

        Returns whether the subscription ever came up, which is what tells
        `run` apart a healthy connection that dropped from a Redis it cannot
        reach at all.
        """
        subscribed = False
        client = redis.from_url(self._redis_url, decode_responses=True)

        try:
            async with client.pubsub() as pubsub:
                await pubsub.subscribe(PRICE_CHANNEL)
                self._connected = True
                subscribed = True
                logger.info("subscribed to %s", PRICE_CHANNEL)

                async for message in pubsub.listen():
                    # `listen` also yields the subscribe/unsubscribe
                    # confirmations, which are not prices.
                    if message.get("type") != "message":
                        continue
                    self._dispatch(message.get("data"))
        finally:
            self._connected = False
            # aclose rather than close: the sync name exists on the async
            # client too and does something else.
            await client.aclose()

        return subscribed

    def _dispatch(self, raw: Any) -> None:
        """Decode one message and fan it out, or log why it went nowhere.

        Nothing in here may raise. This runs inside the `listen` loop, and an
        exception escaping would tear down the subscription for every connected
        client because one producer sent one bad frame.
        """
        event = self._decode(raw)
        if event is None:
            return

        if not self._gate.accept(event.market_id, event.state_version):
            # Not a warning. A duplicate is the expected consequence of a
            # producer retrying, and this is the guard doing its job.
            logger.debug(
                "dropped stale event for market %s at version %s",
                event.market_id,
                event.state_version,
            )
            return

        delivered = self._hub.broadcast(event.market_id, event.frame())
        logger.debug(
            "market %s version %s delivered to %d subscriber(s)",
            event.market_id,
            event.state_version,
            delivered,
        )

    @staticmethod
    def _decode(raw: Any) -> PriceEvent | None:
        """Validate a message onto the contract, or drop it loudly.

        Logged at warning rather than swallowed: a payload that fails here is a
        producer bug, and the symptom on the other side is prices that silently
        stop updating. The log line is the only thing that will tell anybody
        which of the two services is at fault.
        """
        if not isinstance(raw, str):
            logger.warning("ignoring non-text message on %s", PRICE_CHANNEL)
            return None

        try:
            return PriceEvent.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("ignoring malformed price event: %s", exc)
            return None
