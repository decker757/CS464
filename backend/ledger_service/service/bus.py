"""Publishing a price event. [F-9] #112, ADR 0010.

The producer's half of the contract `realtime_service/service/bus.py` defines.
Copied rather than imported — four lines of `redis.publish` are below ADR
0012's bar for `shared/` — and pinned against the original's `PRICE_CHANNEL`
by `unit_test/service/test_price_publish.py`, which reads that file as source
text rather than importing it.

**[T-2] #22 is the caller.** `service/trading.py::execute` calls this after
its own commit — publishing before the commit would announce a price a
rollback then un-makes, and it is called on a fresh, successful trade only:
never on a rejection and never on a replay, both of which move no price.
`unit_test/service/test_price_publish.py::test_nothing_in_this_service_calls_publish`
held that shape for exactly one ticket, the one before this, and #22 deletes
it — its own docstring said it would.

**A publish failure never reaches the caller.** Nothing acknowledges and
nothing subscribes on the producer's behalf, so a trade that has already
committed must not be turned into a failure by a broadcast that never arrived.
The failure is logged with the committed transaction's id — the only field
that leads back to the trade, since a lost broadcast never pages anybody and
the log is the only record it happened — which is why `publish` takes it as a
keyword-only argument rather than the two-argument signature the criteria
describe: `PriceEvent` is `extra="forbid"` over four fields and none of them
is a transaction id, so there is nowhere else for it to travel.

`asyncio.CancelledError` is not caught. It means the process is going away,
not that Redis blipped, and swallowing it would hang a shutdown on a producer
that will not stop.
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as redis

from model.schemas import PriceEvent

logger = logging.getLogger(__name__)

# The channel `realtime_service/service/bus.py` subscribes to. A different
# string publishes successfully into a channel nobody is listening on, so it
# is named here once and documented in `docs/api/realtime-service.md`, the
# same as the original.
PRICE_CHANNEL = "market.price"


async def publish(
    client: redis.Redis, event: PriceEvent, *, transaction_id: uuid.UUID
) -> None:
    """Put one price event on the channel. Never raises.

    `client` is injected rather than built here — one Redis client lives for
    the process, opened on `main.py`'s lifespan, and this is the seam the
    suite drives through with a recorder.
    """
    try:
        await client.publish(PRICE_CHANNEL, event.model_dump_json())
    except Exception:
        logger.warning(
            "failed to publish price event for transaction %s", transaction_id,
            exc_info=True,
        )
