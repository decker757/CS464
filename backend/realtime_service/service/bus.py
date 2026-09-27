"""The Redis side: subscribe once, decode, drop what is stale, hand the rest to the hub. [F-2] #42

One channel for every market rather than one per market; ADR 0010 says why and
when to change that. `publish` is the producer's half of the contract, for the
suite and `publish_test_price.py`; the ledger keeps its own copy (D-048).
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

# Changing this fails silently: the producer's publish still succeeds, into a
# channel nobody reads. The ledger's copy is pinned to this literal (D-048).
PRICE_CHANNEL = "market.price"

# Reconnect backoff. A Redis outage is retried, never fatal: the sockets are
# still open, and dropping them would turn a blip into a reconnect storm.
_BACKOFF_INITIAL_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 10.0


async def publish(client: redis.Redis, event: PriceEvent) -> None:
    """Put one price event on the channel.

    A producer calls this only after its trade commits, and a failure here
    must never fail the trade. ADR 0010.
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
        """Whether the subscription is live.

        `/health` reports it beside `status`, which stays `ok` while this is
        false. docs/api/realtime-service.md.
        """
        return self._connected

    async def run(self) -> None:
        """Consume the channel forever, reconnecting with backoff.

        Returns only on cancellation; every other failure is retried.
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

            # Sleep out here, after `_consume` has closed its client; sleeping
            # in the except branch would hold a dead connection open for the
            # whole backoff. A connection that came up resets the backoff, so
            # only an unreachable Redis is backed off from.
            if subscribed:
                backoff = _BACKOFF_INITIAL_SECONDS

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)

    async def _consume(self) -> bool:
        """One connection, from subscribe until it ends.

        Returns whether the subscription came up, so `run` can tell a dropped
        connection from an unreachable Redis.
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
                    # `listen` also yields subscribe confirmations.
                    if message.get("type") != "message":
                        continue
                    self._dispatch(message.get("data"))
        finally:
            self._connected = False
            # aclose, not close: the sync name exists on the async client too
            # and does something else.
            await client.aclose()

        return subscribed

    def _dispatch(self, raw: Any) -> None:
        """Decode one message and fan it out, or log why it went nowhere.

        Must not raise: it runs inside `listen`, and an exception escaping
        would tear down the subscription for every client.
        """
        event = self._decode(raw)
        if event is None:
            return

        if not self._gate.accept(event.market_id, event.state_version):
            # Debug, not warning: a duplicate is a producer retrying.
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
        """Validate a message onto `PriceEvent`, or log a warning and drop it.

        A warning, because a producer bug shows up only as prices that stop
        updating, and this line is what names the service at fault.
        """
        if not isinstance(raw, str):
            logger.warning("ignoring non-text message on %s", PRICE_CHANNEL)
            return None

        try:
            return PriceEvent.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("ignoring malformed price event: %s", exc)
            return None
