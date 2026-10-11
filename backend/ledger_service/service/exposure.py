"""What resolving a market would cost. [2.2] #6

One SELECT under READ COMMITTED, with no lock: `q`, the pool and
`state_version` all describe the same moment, and no write is decided by this
read. A market with no book reads as empty and opens nothing. It never calls
market_service. DECISIONS.md, "Exposure reads `q` and the pool in one
statement, with no lock" and "Exposure on a market with no book is a 200 with
nothing in it, and writes nothing".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exposure import has_payout_over_pool
from model.entities import MarketBook, MarketOutcome
from service import accounts


@dataclass(frozen=True)
class OutcomeExposure:
    """What one outcome winning would pay, and what the pool would have left."""

    outcome_id: uuid.UUID
    position: int
    shares_outstanding: Decimal
    payout_if_wins: Decimal
    residue_if_wins: Decimal


@dataclass(frozen=True)
class Exposure:
    """A market's exposure as of this read. Every field but `market_id`,
    `payout_exceeds_pool` and `outcomes` is None when there is no book."""

    market_id: uuid.UUID
    opened_at: datetime | None
    state_version: int | None
    seed_subsidy: Decimal | None
    pool: Decimal | None
    payout_exceeds_pool: bool
    outcomes: list[OutcomeExposure]


def _empty(market_id: uuid.UUID) -> Exposure:
    return Exposure(
        market_id=market_id,
        opened_at=None,
        state_version=None,
        seed_subsidy=None,
        pool=None,
        payout_exceeds_pool=False,
        outcomes=[],
    )


async def exposure_of_market(
    session: AsyncSession, *, market_id: uuid.UUID
) -> Exposure:
    """What the market would pay out if each outcome won. Writes nothing.

    Every outcome's payout is one credit per share outstanding, and its residue
    is the pool less that payout.
    """
    pool = accounts.balance_query_of(
        account_id=MarketBook.pool_account_id
    ).scalar_subquery()
    stmt = (
        select(
            MarketBook.opened_at,
            MarketBook.state_version,
            MarketBook.seed_subsidy,
            pool.label("pool"),
            MarketOutcome.outcome_id,
            MarketOutcome.position,
            MarketOutcome.q,
        )
        .join(MarketOutcome, MarketOutcome.market_id == MarketBook.market_id)
        .where(MarketBook.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return _empty(market_id)

    first = rows[0]
    outcomes = [
        OutcomeExposure(
            outcome_id=row.outcome_id,
            position=row.position,
            shares_outstanding=row.q,
            payout_if_wins=row.q,
            residue_if_wins=row.pool - row.q,
        )
        for row in rows
    ]
    return Exposure(
        market_id=market_id,
        opened_at=first.opened_at,
        state_version=first.state_version,
        seed_subsidy=first.seed_subsidy,
        pool=first.pool,
        payout_exceeds_pool=has_payout_over_pool(
            payouts=[outcome.payout_if_wins for outcome in outcomes], pool=first.pool
        ),
        outcomes=outcomes,
    )
