"""`ledger.market_results`, as the database actually builds it. [3.4] #12

One record per finished market (ADR 0019's 2026-10-09 amendment; DECISIONS.md,
"`ledger.market_results` is one record per finished market, with a `kind`").
Asserted against Postgres, for `test_book_schema.py`'s reason. Each refusal is
pinned to the constraint that made it, so a refusal from some other rule does
not pass for this one. Column types and nullability are held by the catalog
comparison in `test_migrations.py`; the CHECK's non-settled half waits for a
second kind ([BE] #221).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA
from unit_test.market_fixtures import market_with_outcomes

LIQUIDITY_B = Decimal("100.0000")


def _entities():
    """`model/entities.py`, reached lazily, so a missing name fails one test
    rather than collection (D-007)."""
    from model import entities  # noqa: PLC0415

    return entities


def _result(market_id: uuid.UUID, outcome_id: uuid.UUID | None):
    return _entities().MarketResult(
        market_id=market_id,
        kind=_entities().ResultKind.SETTLED,
        outcome_id=outcome_id,
        recorded_at=datetime.now(UTC),
    )


def _refusing_constraint(refusal: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The constraint Postgres named when it refused the write. None for a
    not-null violation, which names a column instead."""
    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, and
    # only asyncpg's carries the constraint name.
    return refusal.value.orig.__cause__.constraint_name


async def _primary_key_name(session: AsyncSession) -> str:
    """Read from the catalog rather than assumed, so the test pins which
    constraint refused and not what it is called."""
    return (
        await session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                f"WHERE conrelid = '{SCHEMA}.market_results'::regclass "
                "AND contype = 'p'"
            )
        )
    ).scalar_one()


async def test_a_settled_result_naming_one_of_its_markets_outcomes_is_accepted(
    session: AsyncSession,
) -> None:
    """The row settlement writes. Also red if the CHECK is inverted, if the
    enum stores member names rather than values, or if the composite key's
    columns are crossed."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_result(market_id, outcome_ids[0]))
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketResult).where(
                _entities().MarketResult.market_id == market_id
            )
        )
    ).scalar_one()
    assert stored.outcome_id == outcome_ids[0]


async def test_a_second_result_for_the_same_market_is_refused(
    session: AsyncSession,
) -> None:
    """The primary key on `market_id` stops a market ending twice, even with a
    different winner: a unique on `(market_id, outcome_id)` would accept this."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    session.add(_result(market_id, outcome_ids[0]))
    await session.flush()
    primary_key = await _primary_key_name(session)

    session.add(_result(market_id, outcome_ids[1]))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert _refusing_constraint(refusal) == primary_key
    await session.rollback()


async def test_a_settled_result_with_no_outcome_is_refused(
    session: AsyncSession,
) -> None:
    """A settlement always names its winner. Refused by the paired CHECK, not
    by a NOT NULL: `outcome_id` stays nullable for a void ([BE] #221)."""
    market_id, _ = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_result(market_id, None))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert _refusing_constraint(refusal) == "ck_market_results_outcome_iff_settled"
    await session.rollback()


async def test_a_result_naming_another_markets_outcome_is_refused(
    session: AsyncSession,
) -> None:
    """The winner must be one of this market's outcomes. Both markets have
    books, so only the composite key can refuse it."""
    market_id, _ = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    _, other_outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_result(market_id, other_outcome_ids[0]))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert _refusing_constraint(refusal) == "fk_market_results_market_outcome"
    await session.rollback()
