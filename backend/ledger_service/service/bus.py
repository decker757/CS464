"""Publishing a price event. [F-9] #112, ADR 0010.

The producer's half of `realtime_service/service/bus.py`: copied, not shared
(below ADR 0012's bar), and pinned by `test_price_publish.py`. The trade path
calls it after its commit, on a fresh trade only.

A failure is logged with the transaction id and never raised: the trade has
committed and is correct (D-049). `CancelledError` is not caught; it means
shutdown.
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as redis

from model.schemas import PriceEvent

logger = logging.getLogger(__name__)

# Must match `realtime_service`'s: a different string publishes happily into
# a channel nobody reads.
PRICE_CHANNEL = "market.price"


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
    """Put one price event on the channel. Never raises.

    `client` is the process-wide one from `main.py`'s lifespan (D-047).
    """
    await _publish(
        client, PRICE_CHANNEL, event.model_dump_json(), transaction_id=transaction_id
    )
