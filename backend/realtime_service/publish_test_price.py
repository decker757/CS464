"""Publish one price event by hand, to drive the socket without a trade.

Uses the service's own `PriceEvent` and `publish`, so what it sends is what a
real producer could send.

    cd backend/realtime_service
    .venv/bin/python publish_test_price.py <market-id> [state-version] [yes-price]

With no arguments it invents a market id and prints it. Re-run with a higher
state version to move the price, or a lower one to watch the gate drop it.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import redis.asyncio as redis

from core.config import get_settings
from model.schemas import OutcomePrice, PriceEvent
from service.bus import publish


async def main() -> None:
    market_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else uuid.uuid4()
    state_version = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    yes = Decimal(sys.argv[3]) if len(sys.argv) > 3 else Decimal("0.6234")

    event = PriceEvent(
        market_id=market_id,
        state_version=state_version,
        prices=[
            OutcomePrice(outcome_id=uuid.uuid4(), position=0, price=yes),
            OutcomePrice(outcome_id=uuid.uuid4(), position=1, price=Decimal(1) - yes),
        ],
        occurred_at=datetime.now(UTC),
    )

    client = redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await publish(client, event)
    finally:
        await client.aclose()

    print(f"published market {market_id} at version {state_version}, yes={yes}")
    print(f'  subscribe with: {{"action":"subscribe","market_id":"{market_id}"}}')


if __name__ == "__main__":
    asyncio.run(main())
