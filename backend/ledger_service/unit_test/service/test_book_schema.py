"""The book tables, as the database actually builds them. [F-7] #96.

Asserted against Postgres rather than the model: a constraint SQLAlchemy
declares and the database never received is the failure being guarded, and the
model would agree with itself either way.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


def _books():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import books  # noqa: PLC0415

    return books


def _entities():
    """`model/entities.py`, reached lazily like `_books`."""
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    """`core/errors.py`, reached lazily like `_books`."""
    from core import errors  # noqa: PLC0415

    return errors

ZERO = Decimal(0)


async def _constraints(session: AsyncSession, table: str) -> dict[str, str]:
    """Every constraint Postgres holds on one of this service's tables."""
    rows = (
        await session.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                f"WHERE conrelid = '{SCHEMA}.{table}'::regclass"
            )
        )
    ).all()
    return {name: definition for name, definition in rows}


async def _book(session: AsyncSession, market_id: uuid.UUID, **overrides):
    """A `MarketBook`, backed by a real `MARKET_POOL` account, because
    `pool_account_id` is a foreign key (D-035)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    entities = _entities()
    pool = entities.Account(kind=entities.AccountKind.MARKET_POOL, owner_id=market_id)
    session.add(pool)
    await session.flush()

    now = datetime.now(UTC)
    fields = {
        "market_id": market_id,
        "liquidity_b": Decimal("100.0000"),
        "seed_subsidy": Decimal("250.0000"),
        "pool_account_id": pool.id,
        "state_version": 0,
        "state_changed_at": now,
        "opened_at": now,
    }
    fields.update(overrides)
    return entities.MarketBook(**fields)


async def _open_book(session: AsyncSession, market_id: uuid.UUID) -> None:
    """Persist a book for `market_id`, so outcome rows may reference it
    (a foreign key, D-035)."""
    session.add(await _book(session, market_id))
    await session.flush()


# --- ledger.market_books --------------------------------------------------
async def test_the_market_id_is_the_primary_key(session: AsyncSession) -> None:
    """One book per market: the constraint D-010's race recovery leans on."""
    constraints = await _constraints(session, "market_books")
    primary = [d for d in constraints.values() if d.startswith("PRIMARY KEY")]

    assert primary == ["PRIMARY KEY (market_id)"]


async def test_the_market_id_is_not_a_foreign_key(session: AsyncSession) -> None:
    """It names a row in `market.markets` and cannot point at one (ADR 0003).

    Scoped to `market_id`: `pool_account_id` references this service's own
    table (D-035). A `market_id` foreign key would mean a cross-schema grant.
    """
    constraints = await _constraints(session, "market_books")

    assert not any(
        d.startswith("FOREIGN KEY (market_id)") for d in constraints.values()
    ), "market_id must not reference market.markets"


async def test_state_version_refuses_a_negative_value(session: AsyncSession) -> None:
    """The ticket's `CHECK >= 0`: no code path reaches a negative version, so
    one would mean a stray write broke the realtime ordering."""
    market_id = uuid.uuid4()

    session.add(await _book(session, market_id, state_version=-1))

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()

    await session.rollback()


async def test_state_version_defaults_to_zero(session: AsyncSession) -> None:
    """D-011. A book that has never traded is at version zero, even from an
    insert that does not name the column."""
    market_id = uuid.uuid4()
    from datetime import UTC, datetime  # noqa: PLC0415

    entities = _entities()
    pool = entities.Account(kind=entities.AccountKind.MARKET_POOL, owner_id=market_id)
    session.add(pool)
    await session.flush()

    now = datetime.now(UTC)
    session.add(
        entities.MarketBook(
            market_id=market_id,
            liquidity_b=Decimal("100.0000"),
            seed_subsidy=Decimal("250.0000"),
            pool_account_id=pool.id,
            state_changed_at=now,
            opened_at=now,
        )
    )
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(entities.MarketBook).where(entities.MarketBook.market_id == market_id)
        )
    ).scalar_one()

    assert stored.state_version == 0


async def test_the_pricing_columns_hold_eighteen_significant_digits(
    session: AsyncSession,
) -> None:
    """`Numeric(18, 4)`, matching `market.markets`: a narrower column would
    round the snapshot silently on the way in."""
    exact = Decimal("12345678901234.5678")
    market_id = uuid.uuid4()

    session.add(await _book(session, market_id, liquidity_b=exact, seed_subsidy=exact))
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()

    assert stored.liquidity_b == exact
    assert stored.seed_subsidy == exact


# --- ledger.market_outcomes ----------------------------------------------
async def test_an_outcome_cannot_be_registered_twice_for_one_market(
    session: AsyncSession,
) -> None:
    """The ticket's unique on `(market_id, outcome_id)`: without it a lost
    first-touch race turns a binary market into four well-formed outcomes."""
    market_id, outcome_id = uuid.uuid4(), uuid.uuid4()
    await _open_book(session, market_id)

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=0))
    await session.flush()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=1))

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


async def test_two_outcomes_cannot_share_a_position_in_one_market(
    session: AsyncSession,
) -> None:
    """The ticket's unique on `(market_id, position)`: `position` orders the
    `q` vector, and a duplicate lets two outcomes' prices swap between reads."""
    market_id = uuid.uuid4()
    await _open_book(session, market_id)

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=uuid.uuid4(), position=0))
    await session.flush()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=uuid.uuid4(), position=0))

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


async def test_the_same_outcome_id_may_appear_in_two_different_markets(
    session: AsyncSession,
) -> None:
    """Both constraints are scoped to the market: uniqueness on `outcome_id`
    alone reads as tighter and is the natural mistake."""
    outcome_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    await _open_book(session, first)
    await _open_book(session, second)

    session.add(_entities().MarketOutcome(market_id=first, outcome_id=outcome_id, position=0))
    session.add(_entities().MarketOutcome(market_id=second, outcome_id=outcome_id, position=0))

    await session.flush()

    rows = (
        await session.execute(
            select(_entities().MarketOutcome).where(_entities().MarketOutcome.outcome_id == outcome_id)
        )
    ).scalars()
    assert len(list(rows)) == 2


async def test_q_defaults_to_zero(session: AsyncSession) -> None:
    """A market opens with nothing outstanding, defaulted at the column so a
    row that does not name `q` cannot open with shares in it."""
    market_id, outcome_id = uuid.uuid4(), uuid.uuid4()
    await _open_book(session, market_id)

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=0))
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketOutcome).where(_entities().MarketOutcome.market_id == market_id)
        )
    ).scalar_one()

    assert stored.q == ZERO


async def test_an_outcome_cannot_exist_without_its_book(session: AsyncSession) -> None:
    """D-035: `market_id` references this service's own `market_books`, so an
    outcome orphaned from its book fails loudly."""
    session.add(
        _entities().MarketOutcome(
            market_id=uuid.uuid4(), outcome_id=uuid.uuid4(), position=0
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


async def test_the_outcome_id_is_still_not_a_foreign_key(session: AsyncSession) -> None:
    """The other half of D-035: `outcome_id` names a row in another service's
    schema, so it stays bare (ADR 0003)."""
    definitions = (await _constraints(session, "market_outcomes")).values()
    foreign_keys = [d for d in definitions if d.startswith("FOREIGN KEY")]

    assert any("(market_id)" in d for d in foreign_keys), (
        f"market_id should reference market_books: {foreign_keys}"
    )
    assert not any("(outcome_id)" in d for d in foreign_keys), (
        f"outcome_id must not be a foreign key: {foreign_keys}"
    )


async def test_the_outcome_table_carries_no_label(session: AsyncSession) -> None:
    """"No label — that's display prose the market service owns." A copy here
    would be a second source of truth nothing on this side renders."""
    columns = set(
        (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_schema = '{SCHEMA}' "
                    "AND table_name = 'market_outcomes'"
                )
            )
        ).scalars()
    )

    # The table has to exist first, or "label" is absent from an empty set.
    assert {"market_id", "outcome_id", "position", "q"} <= columns, (
        f"market_outcomes is missing its own columns: {columns}"
    )

    assert "label" not in columns
