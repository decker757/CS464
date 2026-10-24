"""`ledger.market_results`, as the database actually builds it. [3.4] #12

One record per finished market (ADR 0019's 2026-10-09 amendment; DECISIONS.md,
"`ledger.market_results` is one record per finished market, with a `kind`").
Asserted against Postgres, for `test_book_schema.py`'s reason. Each refusal is
pinned to the constraint that made it, so a refusal from some other rule does
not pass for this one. Column types and nullability are held by the catalog
comparison in `test_migrations.py`. A voided row ([BE] #221, ADR 0025) names
no outcome and carries its reason and its administrator, each by its own
CHECK.
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
from unit_test.schema_fixtures import refusing_constraint

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


def _voided(market_id: uuid.UUID, **overrides):
    """The row a void writes: no outcome, a reason and the administrator.
    `overrides` replaces one column, so a refusal names one rule."""
    columns = {
        "kind": _entities().ResultKind.VOIDED,
        "outcome_id": None,
        "reason": "The event was cancelled; there is no result.",
        "voided_by": uuid.uuid4(),
    }
    columns.update(overrides)
    return _entities().MarketResult(
        market_id=market_id, recorded_at=datetime.now(UTC), **columns
    )


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

    assert refusing_constraint(refusal) == primary_key
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

    assert refusing_constraint(refusal) == "ck_market_results_outcome_iff_settled"
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

    assert refusing_constraint(refusal) == "fk_market_results_market_outcome"
    await session.rollback()


async def test_a_voided_result_with_a_reason_an_administrator_and_no_outcome_is_accepted(
    session: AsyncSession,
) -> None:
    """The row a void writes (ADR 0025, ledger revision 0007). Red if either
    new CHECK is inverted, or if the enum has no `voided` member."""
    market_id, _ = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    voided_by = uuid.uuid4()

    session.add(_voided(market_id, voided_by=voided_by))
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketResult).where(
                _entities().MarketResult.market_id == market_id
            )
        )
    ).scalar_one()
    assert stored.kind is _entities().ResultKind.VOIDED
    assert (stored.outcome_id, stored.reason, stored.voided_by) == (
        None,
        "The event was cancelled; there is no result.",
        voided_by,
    )


async def test_a_voided_result_naming_an_outcome_is_refused(
    session: AsyncSession,
) -> None:
    """A void has no winner. The CHECK's non-settled half, which had no second
    kind to test until now."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)

    session.add(_voided(market_id, outcome_id=outcome_ids[0]))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert refusing_constraint(refusal) == "ck_market_results_outcome_iff_settled"
    await session.rollback()


@pytest.mark.parametrize(
    ("kind", "column", "constraint"),
    [
        ("voided", "reason", "ck_market_results_reason_iff_voided"),
        ("settled", "reason", "ck_market_results_reason_iff_voided"),
        ("voided", "voided_by", "ck_market_results_voided_by_iff_voided"),
        ("settled", "voided_by", "ck_market_results_voided_by_iff_voided"),
    ],
)
async def test_a_reason_and_an_administrator_are_set_exactly_on_a_voided_result(
    session: AsyncSession, kind: str, column: str, constraint: str
) -> None:
    """ADR 0025: `reason` and `voided_by` are each set exactly when `kind =
    'voided'`. Both halves of each CHECK: a voided row missing the column, and
    a settled row carrying it. Only that one column differs from a valid row,
    so only its own CHECK can refuse."""
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    if kind == "voided":
        row = _voided(market_id, **{column: None})
    else:
        row = _result(market_id, outcome_ids[0])
        setattr(row, column, "A reason." if column == "reason" else uuid.uuid4())

    session.add(row)

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert refusing_constraint(refusal) == constraint
    await session.rollback()
