"""The two new tables, as the database actually builds them. [F-7] #96.

The ticket specifies these columns and constraints precisely, and every one of
them is load-bearing somewhere else in the suite: the primary key on
`market_id` is what turns D-010's race into a recoverable `IntegrityError`, and
the two unique constraints on `market_outcomes` are the only thing stopping
that race from writing four outcome rows for a binary market.

Asserted against Postgres rather than against `model/entities.py`, because a
`UniqueConstraint` that SQLAlchemy declares and the database never received is
exactly the failure these are guarding, and reading the model back would agree
with itself either way.

These tables reach `cs464` by `create_all` on the next restart and need no
migration — they are new, and `create_all` only ever issues CREATE TABLE IF NOT
EXISTS. That is the whole of the ticket's Notes on the subject, and it is worth
being clear that it holds *because* they are new. Adding a column to either one
later is the usual story and needs a hand-applied `ALTER TABLE`.
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
    """Imported inside each test rather than at module scope.

    `service/books.py` does not exist yet, and a top-level import would be one
    collection error taking the whole file down as a single red line. Reached
    through here, every test fails on its own, named after the criterion it
    holds (D-007). Same convention as `market_service`'s `test_browsing.py`.
    """
    from service import books  # noqa: PLC0415

    return books


def _entities():
    """`model/entities.py` exists; `MarketBook` and `MarketOutcome` do not yet."""
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    """`core/errors.py` exists; the book errors below do not yet."""
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


def _book(market_id: uuid.UUID, pool_account_id: uuid.UUID, **overrides):
    from datetime import UTC, datetime  # noqa: PLC0415

    now = datetime.now(UTC)
    fields = {
        "market_id": market_id,
        "liquidity_b": Decimal("100.0000"),
        "seed_subsidy": Decimal("250.0000"),
        "pool_account_id": pool_account_id,
        "state_version": 0,
        "state_changed_at": now,
        "opened_at": now,
    }
    fields.update(overrides)
    return _entities().MarketBook(**fields)


# --- ledger.market_books --------------------------------------------------
async def test_the_market_id_is_the_primary_key(session: AsyncSession) -> None:
    """One book per market, enforced by the database.

    This is the constraint D-010 leans on. Two concurrent first-touches both
    insert; this is what makes the second one an `IntegrityError` the caller
    can recover from rather than a second book nobody notices.
    """
    constraints = await _constraints(session, "market_books")
    primary = [d for d in constraints.values() if d.startswith("PRIMARY KEY")]

    assert primary == ["PRIMARY KEY (market_id)"]


async def test_the_market_id_is_not_a_foreign_key(session: AsyncSession) -> None:
    """It names a row in `market.markets` and it cannot point at one.

    `ledger_svc` holds no grant on the market schema — `test_schema_boundary.py`
    asserts the denial directly — so a foreign key here would fail to create.
    The same trade `Account.owner_id` already makes for a user id, and ADR 0003
    is why: a join across a service boundary should fail loudly rather than
    quietly work.

    If this ever passes with a foreign key present, somebody has added a
    cross-schema grant and two services have been welded together.
    """
    constraints = await _constraints(session, "market_books")

    assert not any(d.startswith("FOREIGN KEY") for d in constraints.values()), (
        "market_id must not reference market.markets"
    )


async def test_state_version_refuses_a_negative_value(session: AsyncSession) -> None:
    """The ticket's `CHECK >= 0`.

    `state_version` only ever increases, one per trade, under the book's row
    lock. A negative value is not a state this service can reach by any code
    path, which is exactly why it is worth a constraint: if one ever appears,
    something has written to this column that should not have, and the realtime
    contract's ordering guarantee is broken for that market.
    """
    market_id, pool = uuid.uuid4(), uuid.uuid4()

    session.add(_book(market_id, pool, state_version=-1))

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()

    await session.rollback()


async def test_state_version_defaults_to_zero(session: AsyncSession) -> None:
    """D-011. A book that has never traded is at version zero.

    Asserted against a row built without naming the column, so this holds for
    an insert that does not mention `state_version` at all — which is what the
    server default is for, and what a hand-written INSERT during an incident
    would rely on.
    """
    market_id, pool = uuid.uuid4(), uuid.uuid4()
    from datetime import UTC, datetime  # noqa: PLC0415

    now = datetime.now(UTC)
    session.add(
        _entities().MarketBook(
            market_id=market_id,
            liquidity_b=Decimal("100.0000"),
            seed_subsidy=Decimal("250.0000"),
            pool_account_id=pool,
            state_changed_at=now,
            opened_at=now,
        )
    )
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()

    assert stored.state_version == 0


async def test_the_pricing_columns_hold_eighteen_significant_digits(
    session: AsyncSession,
) -> None:
    """`Numeric(18, 4)`, matching `market.markets` on the other side of the wire.

    The two arithmetics have to agree down to the last place: a subsidy an
    administrator promised over there is a balance somebody spends here. A
    narrower column would round the snapshot on the way in and there would be
    no error to see it happen.

    The value is the same eighteen-digit one the parsing tests use, for the
    same reason — it is the smallest realistic value that a float path or a
    short column cannot reproduce.
    """
    exact = Decimal("12345678901234.5678")
    market_id, pool = uuid.uuid4(), uuid.uuid4()

    session.add(_book(market_id, pool, liquidity_b=exact, seed_subsidy=exact))
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
    """The ticket's unique on `(market_id, outcome_id)`.

    Without it the losing callers of a first-touch race write their outcome
    rows beside the winner's, and the next price calculation reads a binary
    market as having four outcomes. Every `q` would still be zero, so the
    prices would still sum to one — at 0.25 each. A wrong answer that looks
    entirely well formed.
    """
    market_id, outcome_id = uuid.uuid4(), uuid.uuid4()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=0))
    await session.flush()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=1))

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


async def test_two_outcomes_cannot_share_a_position_in_one_market(
    session: AsyncSession,
) -> None:
    """The ticket's unique on `(market_id, position)`.

    `position` is what orders the `q` vector handed to the pricing engine, and
    the engine takes a plain sequence — it has no way to notice that two
    entries claim to be the same slot. Duplicated, the ordering becomes
    whatever the query returns, so a market's YES and NO prices could swap
    between two reads with nothing written in between.
    """
    market_id = uuid.uuid4()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=uuid.uuid4(), position=0))
    await session.flush()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=uuid.uuid4(), position=0))

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


async def test_the_same_outcome_id_may_appear_in_two_different_markets(
    session: AsyncSession,
) -> None:
    """Both constraints are scoped to the market, and that is deliberate.

    Outcome ids are generated by the market service and are unique there
    already, so this is not a case anyone expects to see. It is asserted
    because the natural mistake when writing these constraints is to put the
    uniqueness on `outcome_id` alone, which reads as tighter and would quietly
    make the second market in the system unopenable if ids ever collided.
    """
    outcome_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()

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
    """A market opens with nothing outstanding, which is what makes the opening
    price uniform: `C(q)` at `q = 0` gives every outcome `1/n`.

    Defaulted at the column rather than set by the caller, so a row written
    without naming it cannot open a market that already has shares in it.
    """
    market_id, outcome_id = uuid.uuid4(), uuid.uuid4()

    session.add(_entities().MarketOutcome(market_id=market_id, outcome_id=outcome_id, position=0))
    await session.flush()
    session.expire_all()

    stored = (
        await session.execute(
            select(_entities().MarketOutcome).where(_entities().MarketOutcome.market_id == market_id)
        )
    ).scalar_one()

    assert stored.q == ZERO


async def test_the_outcome_table_carries_no_label(session: AsyncSession) -> None:
    """"No label — that's display prose the market service owns."

    A label copied here is a second source of truth for a string that the
    market service can edit on a draft and that nothing on this side renders.
    Asserted against the database's own column list, because the way this
    arrives is somebody adding it to the model for convenience during [T-2]
    #22 and nothing objecting.
    """
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

    # The table has to exist first. A missing table returns no rows, and
    # "label" is absent from the empty set just as convincingly as from a
    # correct one — so without this the test is green before the table is
    # written and stays green if it is ever dropped.
    assert {"market_id", "outcome_id", "position", "q"} <= columns, (
        f"market_outcomes is missing its own columns: {columns}"
    )

    assert "label" not in columns
