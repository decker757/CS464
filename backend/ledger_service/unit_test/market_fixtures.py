"""A persisted book, for the schema tests that need a market to point at.

`test_positions_schema.py` and `test_result_schema.py` each reference a market
and its outcomes through a composite foreign key, and neither is testing how
the book is built. Project modules are reached inside the function, so a missing
name fails one test rather than collection (D-007).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession


async def market_with_outcomes(
    session: AsyncSession, *, liquidity_b: Decimal
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A book with two outcomes, flushed so a row can reference them. Returns
    the market id and its outcome ids, in position order."""
    from model import entities  # noqa: PLC0415

    market_id = uuid.uuid4()

    pool = entities.Account(kind=entities.AccountKind.MARKET_POOL, owner_id=market_id)
    session.add(pool)
    await session.flush()

    now = datetime.now(UTC)
    session.add(
        entities.MarketBook(
            market_id=market_id,
            liquidity_b=liquidity_b,
            seed_subsidy=Decimal("250.0000"),
            pool_account_id=pool.id,
            state_version=0,
            state_changed_at=now,
            opened_at=now,
        )
    )
    await session.flush()

    outcome_ids = [uuid.uuid4(), uuid.uuid4()]
    for position, outcome_id in enumerate(outcome_ids):
        session.add(
            entities.MarketOutcome(
                market_id=market_id, outcome_id=outcome_id, position=position
            )
        )
    await session.flush()
    return market_id, outcome_ids
