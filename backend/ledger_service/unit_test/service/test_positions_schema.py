"""`ledger.positions`, as the database actually builds it. [T-2] #22

Asserted against Postgres rather than against `model/entities.py`, for
`test_book_schema.py`'s reason: a constraint SQLAlchemy declares and the
database never received is exactly the failure these are guarding, and reading
the model back would agree with itself either way.

This table reaches the long-lived `cs464` through `create_all` at startup,
which issues `CREATE TABLE IF NOT EXISTS`, so it needs no file in
`sql/migrations/` — the trap CLAUDE.md documents is a new *column* on a table
that already exists, and this is a new table. Same for
`TransactionKind.TRADE_BUY`, which is a member of a non-native `Enum` and so a
Python change and nothing else. Both are verified below rather than trusted,
with CLAUDE.md's own `pg_constraint` query pointed at this table.

**The one thing that must not be here is a trigger.** `ledger.entries` is
append-only and ships with one; this table is a rollup that gets UPDATEd on
every second buy, reconstructible from the entries, and the entries stay the
record. A copy-pasted trigger would make the second buy on any outcome a 500,
so the absence is asserted rather than assumed.
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
    """`model/entities.py` exists; `Position` does not yet."""
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
    """A book and one outcome, committed far enough to be referenced.

    `(market_id, outcome_id)` is a composite foreign key into
    `ledger.market_outcomes`, so a position written against a fabricated pair
    fails on that constraint rather than on whichever one the calling test is
    about.
    """
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
    """`(user_id, market_id, outcome_id)`, and not a surrogate id.

    The same argument `MarketOutcome`'s pair makes: nothing references a
    position by an identity of its own, and [T-4] #24 reaches them by the
    triple. It is also what makes "#22 only ever adds to a position" one row
    rather than a new row per trade.
    """
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
    """`(market_id, outcome_id)` references `ledger.market_outcomes`.

    This service's own table, that exact pair being its primary key, so it
    gets a constraint — the same reasoning "`pool_account_id` is a foreign
    key; `market_id` still is not" already applied. A position orphaned from
    the outcome it is a position *in* is a holding with nothing to settle
    against, and it is the kind of mistake this ticket could introduce with
    nothing going red.
    """
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
    """It names a row in `auth.users` and it cannot point at one.

    `ledger_svc` holds no grant on the auth schema, so a foreign key here
    would fail to create. Same trade `Account.owner_id` already makes, and ADR
    0003 is why. If this ever passes with one present, somebody has added a
    cross-schema grant and two services have been welded together.
    """
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
    """"Shares keep money's scale of 4, in `q` and in positions."

    A quantity arrives at scale 4 and addition at scale 4 is closed, so the
    quantity a trader was quoted for is the quantity that is written with no
    rounding anywhere between the quote and the row. It also has to match
    `core/lmsr.py`'s `_PRECISION`, which is derived from `Numeric(18, 4)` and
    says so.
    """
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
    """`CHECK (quantity >= 0)` and `CHECK (cost_basis >= 0)`.

    `quantity >= 0` is the no-shorting rule per user, the counterpart to
    `InsufficientSharesOutstanding`'s per-outcome one. [T-3] #23 enforces it
    under the book lock; this is the backstop, not the check — and a backstop
    that was declared and never created is not a backstop.
    """
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
    """No trigger, and the absence is the assertion.

    `ledger.entries` ships with a statement-level trigger refusing UPDATE,
    DELETE and TRUNCATE, attached as an `after_create` DDL event — so it is
    exactly one copied line away from landing here too. It must not: this is a
    rollup that is UPDATEd on every second buy into the same outcome, and a
    trigger would turn that into a 500 on a path the suite might only reach in
    one test.
    """
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
    """`TRADE_BUY` is a member of a non-native `Enum`, so the column is a
    plain `varchar` with no CHECK and adding a member is a Python change.

    Verified rather than trusted, which is what CLAUDE.md asks for: the same
    `pg_constraint` query, pointed at this service's table. A CHECK appearing
    here would mean a member added in Python is a runtime failure on every
    trade until somebody hand-applies an ALTER against `cs464`.
    """
    assert _entities().TransactionKind.TRADE_BUY.value == "trade_buy"

    constraints = await _constraints(session, "transactions")
    assert not any(
        "kind" in d and d.startswith("CHECK") for d in constraints.values()
    ), constraints
