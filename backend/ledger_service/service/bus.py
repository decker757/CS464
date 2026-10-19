"""Publishing a price event, and an order event per fill. [F-9] #112, [T-7] #27, ADR 0010.

The producer's half of `realtime_service/service/bus.py`: copied, not shared
(below ADR 0012's bar), and pinned by `test_price_publish.py`. The trade path
and a placement that fills call it after their commit, on a fresh request only.

A failed publish is logged with the transaction id and never raised: the
trade has committed and is correct (D-049). Only the Redis call is guarded;
serialising the event is not. `CancelledError` is not caught; it means
shutdown.
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as redis

from model.schemas import OrderEvent, PriceEvent

logger = logging.getLogger(__name__)

# Both must match `realtime_service`'s: a different string publishes happily
# into a channel nobody reads.
PRICE_CHANNEL = "market.price"
ORDER_CHANNEL = "order.update"


async def _publish(
    client: redis.Redis, channel: str, payload: str, *, transaction_id: uuid.UUID
) -> None:
    """Put one payload on `channel`, logging a failure rather than raising it."""
    try:
        await client.publish(channel, payload)
    except Exception:
        logger.warning(
            "failed to publish on %s for transaction %s", channel, transaction_id,
            exc_info=True,
        )


async def publish(
    client: redis.Redis, event: PriceEvent, *, transaction_id: uuid.UUID
) -> None:
    """Put one price event on the channel. A failed publish is logged, never
    raised; serialising `event` is outside that guard.

    `client` is the process-wide one from `main.py`'s lifespan (D-047).
    """
    await _publish(
        client, PRICE_CHANNEL, event.model_dump_json(), transaction_id=transaction_id
    )


async def publish_order(
    client: redis.Redis, event: OrderEvent, *, transaction_id: uuid.UUID
) -> None:
    """Put one order event on its channel. A failed publish is logged, never
    raised; serialising `event` is outside that guard.

    `transaction_id` is the fill's, the one id a log line can lead back to.
    """
    await _publish(
        client, ORDER_CHANNEL, event.model_dump_json(), transaction_id=transaction_id
    )
