"""Fixtures the [T-3] #23 test files share.

Built on `trade_fixtures.py` rather than beside it: the same `Upstream`, the
same `Recorder`, the same `buy` driver with `side="sell"`, the same lazy
module handles. What is added here is only what a sell needs and a buy did
not.

**Every book here is opened at zero, by its first touch.** Never `warm()`,
whose `set_q` writes `q` directly. #23's invariants — each outcome's `q`
equals the sum of positions in it, and the pool never holds less than its
seed subsidy — are both false on a book whose `q` was written by hand: that
`q` is above the positions and was never paid for. "Shares outstanding equal
the sum of positions, so the outstanding check is a backstop on the trade
route" says so in its Notes. Every holding is created by a real buy. The one
test that writes `q` directly is the backstop's own, and it does so in its
own body, not through anything here.

**The numbers are the issue's worked example, reached through real trades.**
At `b = 137`, buying `54.3333` of outcome 0 on a book opened at zero costs a
raw `29.84275…`: charged `29.8428` by the ceiling (a floor would charge
`29.8427`), and that is the basis. Selling `10.0000` of it pays `5.8905` (a
ceiling would pay `5.8906`) and leaves a basis of `24.3503` (a floor would
leave `24.3502`). So the rounding direction of the buy, of the proceeds and of
the remaining basis each change a number these tests pin.
`assert_sell_rounding_is_load_bearing` and `assert_basis_rounding_is_load_bearing`
prove that property of any other constant a test picks.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    B,
    QUANTUM,
    ZERO,
    Upstream,
    books,
    buy,
    entities,
    errors,
    expected_total,
    lmsr,
    pricing,
    raw_cost,
    session_factory,
    token,
)

# --- the worked example, pinned ------------------------------------------
#
# Pinned as literals, not recomputed, because the issue states them as
# literals: these are the numbers a reviewer checks the criterion against.
# `test_sell.py::test_the_worked_example_constants_are_what_the_engine_says`
# holds them against `core/lmsr.py`, so they cannot drift from the engine
# without a test saying which one moved.
HELD = Decimal("54.3333")
BASIS = Decimal("29.8428")
SOLD = Decimal("10.0000")
PROCEEDS = Decimal("5.8905")
REMAINING_QUANTITY = Decimal("44.3333")
REMAINING_BASIS = Decimal("24.3503")
TICK = Decimal("0.0001")

# Two sells, each within `HELD` and together over it (41 + 29.7777 = 70.7777).
RACE_SELLS = (Decimal("41.0000"), Decimal("29.7777"))

AT_ZERO = (ZERO, ZERO)


# --- lazy handles --------------------------------------------------------
def release_basis():
    """`core/pricing.py::release_basis`, [T-3] #23's pure rule. Does not
    exist yet."""
    return pricing().release_basis


def snapshot_module():
    from service import snapshot  # noqa: PLC0415

    return snapshot


# --- the book ------------------------------------------------------------
async def open_at_zero(session: AsyncSession, upstream: Upstream):
    """The first touch, and nothing after it. `q` is zero in every outcome,
    the pool holds exactly the seed subsidy, and `state_version` is 0.

    The upstream's counters are reset afterwards so a test counting the
    trade's own hops does not count the book's.
    """
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        transport=upstream.transport,
    )
    upstream.calls = 0
    upstream.tokens.clear()
    return book


async def version_of(session: AsyncSession, market_id: uuid.UUID) -> int:
    """The book's current `state_version`, read without the identity map, so
    another session's commit is visible."""
    book = entities().MarketBook
    return (
        await session.execute(
            select(book.state_version).where(book.market_id == market_id)
        )
    ).scalar_one()


async def hold(
    session: AsyncSession,
    upstream: Upstream,
    *,
    user_id: uuid.UUID,
    quantity: Decimal = HELD,
    outcome: int = 0,
    client_key: str | None = None,
):
    """A real buy at whatever the book's version currently is. This is how
    every holding in #23's suite comes to exist."""
    return await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=outcome,
        quantity=quantity,
        side="buy",
        state_version=await version_of(session, upstream.market_id),
        client_key=client_key or f"hold-{uuid.uuid4()}",
    )


async def sell(
    session: AsyncSession,
    upstream: Upstream,
    *,
    user_id: uuid.UUID,
    quantity: Decimal = SOLD,
    outcome: int = 0,
    state_version: int | None = None,
    client_key: str | None = None,
    **kwargs,
):
    """`buy(..., side="sell")`, quoting the current version unless told
    otherwise. The one place a sell is spelled."""
    if state_version is None:
        state_version = await version_of(session, upstream.market_id)
    return await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=outcome,
        quantity=quantity,
        side="sell",
        state_version=state_version,
        client_key=client_key or f"sell-{uuid.uuid4()}",
        **kwargs,
    )


# --- reading positions ----------------------------------------------------
async def positions_by_outcome(
    session: AsyncSession, market_id: uuid.UUID
) -> list[Decimal]:
    """The sum of every user's position in each outcome, in `position`
    order. An outcome nobody holds sums to zero rather than disappearing."""
    ents = entities()
    rows = (
        await session.execute(
            select(
                ents.MarketOutcome.position,
                func.coalesce(func.sum(ents.Position.quantity), ZERO),
            )
            .select_from(ents.MarketOutcome)
            .join(
                ents.Position,
                (ents.Position.market_id == ents.MarketOutcome.market_id)
                & (ents.Position.outcome_id == ents.MarketOutcome.outcome_id),
                isouter=True,
            )
            .where(ents.MarketOutcome.market_id == market_id)
            .group_by(ents.MarketOutcome.position)
            .order_by(ents.MarketOutcome.position)
        )
    ).all()
    return [total for _, total in rows]


async def q_now(session: AsyncSession, market_id: uuid.UUID) -> list[Decimal]:
    outcome = entities().MarketOutcome
    return list(
        (
            await session.execute(
                select(outcome.q)
                .where(outcome.market_id == market_id)
                .order_by(outcome.position)
            )
        ).scalars()
    )


async def assert_q_matches_positions(
    session: AsyncSession, market_id: uuid.UUID
) -> None:
    """"Shares outstanding equal the sum of positions", per outcome."""
    q = await q_now(session, market_id)
    held = await positions_by_outcome(session, market_id)
    assert q == held, f"q {q} is not the sum of positions {held}"


async def holding(
    session: AsyncSession,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
) -> tuple[Decimal, Decimal] | None:
    """`(quantity, cost_basis)`, or None when there is no row at all — which
    "A position sold to zero keeps its row" makes a different answer from
    `(0, 0)`."""
    position = entities().Position
    row = (
        await session.execute(
            select(position.quantity, position.cost_basis).where(
                position.user_id == user_id,
                position.market_id == market_id,
                position.outcome_id == outcome_id,
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


# --- "writes nothing", counted twice --------------------------------------
@dataclass(frozen=True)
class Footprint:
    """Everything a trade could write, read with column selects so the
    identity map cannot answer instead of the database."""

    transactions: int
    entries: int
    q: tuple[Decimal, ...]
    state_version: int
    positions: tuple[tuple[str, str, Decimal, Decimal], ...]


async def footprint(session: AsyncSession, market_id: uuid.UUID) -> Footprint:
    ents = entities()
    transactions = (
        await session.execute(select(func.count()).select_from(ents.Transaction))
    ).scalar_one()
    entries = (
        await session.execute(select(func.count()).select_from(ents.Entry))
    ).scalar_one()
    positions = (
        await session.execute(
            select(
                ents.Position.user_id,
                ents.Position.outcome_id,
                ents.Position.quantity,
                ents.Position.cost_basis,
            )
            .where(ents.Position.market_id == market_id)
            .order_by(ents.Position.user_id, ents.Position.outcome_id)
        )
    ).all()
    return Footprint(
        transactions=transactions,
        entries=entries,
        q=tuple(await q_now(session, market_id)),
        state_version=await version_of(session, market_id),
        positions=tuple((str(u), str(o), qty, basis) for u, o, qty, basis in positions),
    )


async def assert_wrote_nothing(
    session: AsyncSession, market_id: uuid.UUID, before: Footprint
) -> None:
    """A refused request wrote nothing — asked twice, and the first answer is
    the one that catches write-then-refuse.

    **In the request's own session, before any rollback.** The factory runs
    with `autoflush=False`, so a write the path made and then abandoned can be
    in one of two places: pending on an ORM object, or flushed into this
    session's still-open transaction. The first is asked of the session's own
    bookkeeping, the second with column selects that see this transaction's
    uncommitted rows. A path that writes `q`, the position or the version and
    only then refuses is caught here even though its rollback would hide it.

    **Then from a second session, after the rollback.** That catches the other
    failure: a write that was committed before the refusal, which a rollback
    cannot undo.
    """
    pending = (
        list(session.new)
        + [o for o in session.dirty if session.is_modified(o)]
        + list(session.deleted)
    )
    assert pending == [], f"the refused request left pending writes: {pending}"
    assert await footprint(session, market_id) == before, (
        "the refused request flushed a write into its own transaction before "
        "refusing"
    )

    await session.rollback()

    async with session_factory()() as other:
        assert await footprint(other, market_id) == before, (
            "the refused request committed a write"
        )


# --- the expected arithmetic ----------------------------------------------
def expected_proceeds(
    q: Sequence[Decimal], outcome: int, quantity: Decimal
) -> Decimal:
    """Positive, floored: `trade_fixtures.expected_total` on the sell side."""
    return expected_total(q, outcome, "sell", quantity)


def ceiled_proceeds(q: Sequence[Decimal], outcome: int, quantity: Decimal) -> Decimal:
    """What a sell would pay if it rounded the wrong way."""
    return raw_cost(q, outcome, "sell", quantity).copy_abs().quantize(
        QUANTUM, rounding=ROUND_CEILING
    )


def assert_sell_rounding_is_load_bearing(
    q: Sequence[Decimal], outcome: int, quantity: Decimal
) -> None:
    """The sell counterpart of `trade_fixtures.assert_rounding_is_load_bearing`."""
    magnitude = raw_cost(q, outcome, "sell", quantity).copy_abs()
    down = magnitude.quantize(QUANTUM, rounding=ROUND_FLOOR)
    up = magnitude.quantize(QUANTUM, rounding=ROUND_CEILING)
    assert down != up, (
        f"the proceeds of selling {quantity} against q={list(q)} at b={B} are "
        f"{magnitude}, exact at scale 4 — floor and ceiling agree and the "
        "rounding assertion is a tautology"
    )


def remaining_basis(basis: Decimal, held: Decimal, quantity: Decimal) -> Decimal:
    """The rule in "A sell releases cost basis at average cost", restated
    from the criterion rather than imported, so the path tests check the
    implementation against the sentence and not against itself."""
    if quantity == held:
        return Decimal("0.0000")
    with lmsr()._engine_context():
        exact = basis * (held - quantity) / held
    return exact.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def assert_basis_rounding_is_load_bearing(
    basis: Decimal, held: Decimal, quantity: Decimal
) -> None:
    with lmsr()._engine_context():
        exact = basis * (held - quantity) / held
    half_up = exact.quantize(QUANTUM, rounding=ROUND_HALF_UP)
    floor = exact.quantize(QUANTUM, rounding=ROUND_FLOOR)
    assert half_up != floor, (
        f"{basis} over {held} selling {quantity} leaves {exact}, where half-up "
        "and floor agree — the basis rounding assertion is a tautology"
    )


def insufficient_shares_held():
    return errors().InsufficientSharesHeld
