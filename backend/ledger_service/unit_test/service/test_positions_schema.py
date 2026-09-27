"""`ledger.positions`, as the database actually builds it. [T-2] #22

Asserted against Postgres, for `test_book_schema.py`'s reason. The one thing
that must not be here is `ledger.entries`' append-only trigger: this table is
a rollup that is UPDATEd on every repeat buy.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SCHEMA


def _entities():
    """`model/entities.py`, reached lazily (D-007)."""
    from model import entities  # noqa: PLC0415

    return entities


async def _constraints(session: AsyncSession, table: str) -> dict[str, str]:
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


async def _columns(session: AsyncSession, table: str) -> dict[str, tuple]:
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, "
                "numeric_scale, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": SCHEMA, "table": table},
        )
    ).all()
    return {r[0]: tuple(r[1:]) for r in rows}


async def _market(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """A book and its outcomes, flushed so a position can reference them
    through the composite foreign key."""
    from datetime import UTC, datetime  # noqa: PLC0415

    entities = _entities()
    market_id = uuid.uuid4()
    outcome_id = uuid.uuid4()

    pool = entities.Account(kind=entities.AccountKind.MARKET_POOL, owner_id=market_id)
    session.add(pool)
    await session.flush()

    now = datetime.now(UTC)
    session.add(
        entities.MarketBook(
            market_id=market_id,
            liquidity_b=Decimal("137.0000"),
            seed_subsidy=Decimal("250.0000"),
            pool_account_id=pool.id,
            state_version=0,
            state_changed_at=now,
            opened_at=now,
        )
    )
    session.add(
        entities.MarketOutcome(
            market_id=market_id, outcome_id=outcome_id, position=0
        )
    )
    session.add(
        entities.MarketOutcome(
            market_id=market_id, outcome_id=uuid.uuid4(), position=1
        )
    )
    await session.flush()
    return market_id, outcome_id


def _position(user_id: uuid.UUID, market_id: uuid.UUID, outcome_id: uuid.UUID, **over):
    entities = _entities()
    fields = {
        "user_id": user_id,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "quantity": Decimal("41.0000"),
        "cost_basis": Decimal("35.0717"),
    }
    fields.update(over)
    return entities.Position(**fields)


# =========================================================================
# The key
# =========================================================================
async def test_the_primary_key_is_the_triple(session: AsyncSession) -> None:
    """`(user_id, market_id, outcome_id)`, not a surrogate id: one row per
    holding, reached by the triple."""
    constraints = await _constraints(session, "positions")
    primary = [d for d in constraints.values() if d.startswith("PRIMARY KEY")]

    assert primary == ["PRIMARY KEY (user_id, market_id, outcome_id)"]


async def test_one_user_holds_two_outcomes_as_two_rows(
    session: AsyncSession,
) -> None:
    """The key's own consequence, exercised. A trader on both sides of a
    binary market holds two positions, and a key of `(user_id, market_id)`
    would merge them into one row at an average price that means nothing."""
    market_id, outcome_id = await _market(session)
    user_id = uuid.uuid4()
    other = (
        await session.execute(
            text(
                f"SELECT outcome_id FROM {SCHEMA}.market_outcomes "
                "WHERE market_id = :m AND position = 1"
            ),
            {"m": market_id},
        )
    ).scalar_one()

    session.add(_position(user_id, market_id, outcome_id))
    session.add(_position(user_id, market_id, other))
    await session.flush()


# =========================================================================
# The foreign keys, and the one that is deliberately absent
# =========================================================================
async def test_the_market_and_outcome_are_a_composite_foreign_key(
    session: AsyncSession,
) -> None:
    """`(market_id, outcome_id)` references this service's own
    `ledger.market_outcomes` (D-035's reasoning), so a position cannot be
    orphaned from its outcome."""
    constraints = await _constraints(session, "positions")

    composite = [
        d
        for d in constraints.values()
        if d.startswith("FOREIGN KEY (market_id, outcome_id)")
    ]
    assert composite, constraints
    assert "market_outcomes" in composite[0]


async def test_a_position_in_an_outcome_that_does_not_exist_is_refused(
    session: AsyncSession,
) -> None:
    """The constraint doing its job, rather than the catalogue reporting it."""
    market_id, _ = await _market(session)

    session.add(_position(uuid.uuid4(), market_id, uuid.uuid4()))

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()
    await session.rollback()


async def test_the_user_id_is_not_a_foreign_key(session: AsyncSession) -> None:
    """It names a row in `auth.users` and cannot point at one (ADR 0003)."""
    constraints = await _constraints(session, "positions")

    assert not any(
        d.startswith("FOREIGN KEY (user_id)") for d in constraints.values()
    ), "user_id must not reference auth.users"


# =========================================================================
# The columns
# =========================================================================
async def test_quantity_and_cost_basis_are_numeric_18_4(
    session: AsyncSession,
) -> None:
    """"Shares keep money's scale of 4, in `q` and in positions", so a quoted
    quantity is written with no rounding in between."""
    columns = await _columns(session, "positions")

    assert columns["quantity"][:4] == ("numeric", 18, 4, "NO")
    assert columns["cost_basis"][:4] == ("numeric", 18, 4, "NO")


async def test_the_timestamps_are_both_present(session: AsyncSession) -> None:
    """`created_at` and `updated_at`. The second one is what separates this
    table from every other one in this schema: positions are the only rows
    here that change after they are written."""
    columns = await _columns(session, "positions")

    assert "created_at" in columns
    assert "updated_at" in columns


@pytest.mark.parametrize("column", ["quantity", "cost_basis"])
async def test_a_negative_value_is_refused(
    session: AsyncSession, column: str
) -> None:
    """`CHECK (quantity >= 0)` and `CHECK (cost_basis >= 0)`: the database's
    backstop for the per-user no-shorting rule."""
    market_id, outcome_id = await _market(session)

    session.add(
        _position(uuid.uuid4(), market_id, outcome_id, **{column: Decimal("-1.0000")})
    )

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()
    await session.rollback()


async def test_a_zero_position_is_allowed(session: AsyncSession) -> None:
    """`>= 0`, not `> 0`. [T-3] #23 sells a position down and a trader who
    sold everything holds zero shares, which is a fact rather than an error —
    and their `cost_basis` history is still worth keeping."""
    market_id, outcome_id = await _market(session)

    session.add(
        _position(
            uuid.uuid4(),
            market_id,
            outcome_id,
            quantity=Decimal("0.0000"),
            cost_basis=Decimal("0.0000"),
        )
    )
    await session.flush()


# =========================================================================
# What this table is not
# =========================================================================
async def test_the_table_is_not_append_only(session: AsyncSession) -> None:
    """No trigger: `ledger.entries`' append-only one is a copied line away,
    and would make every repeat buy a 500."""
    market_id, outcome_id = await _market(session)
    user_id = uuid.uuid4()
    session.add(_position(user_id, market_id, outcome_id))
    await session.flush()

    await session.execute(
        text(
            f"UPDATE {SCHEMA}.positions SET quantity = quantity + 1 "
            "WHERE user_id = :u AND market_id = :m AND outcome_id = :o"
        ),
        {"u": user_id, "m": market_id, "o": outcome_id},
    )

    triggers = (
        await session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                f"WHERE tgrelid = '{SCHEMA}.positions'::regclass "
                "AND NOT tgisinternal"
            )
        )
    ).scalars()
    assert list(triggers) == [], (
        "ledger.positions is a rollup and must not carry ledger.entries' "
        "append-only trigger"
    )


async def test_the_transaction_kind_needs_no_migration(
    session: AsyncSession,
) -> None:
    """`TRADE_BUY` is a member of a non-native `Enum`, so adding one is a
    Python change. Verified with CLAUDE.md's `pg_constraint` query."""
    assert _entities().TransactionKind.TRADE_BUY.value == "trade_buy"

    constraints = await _constraints(session, "transactions")
    assert not any(
        "kind" in d and d.startswith("CHECK") for d in constraints.values()
    ), constraints
