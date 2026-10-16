"""Fixtures the [2.2] #6 exposure tests share.

Built on `sell_fixtures.py`: every book here is opened by its first touch,
`books.ensure_open`, so the pool really holds the seed through its
MARKET_SEED entry, and every share outstanding was bought through
`trading.execute`. Never `warm()`: the `set_q` in `unit_test/conftest.py` writes
`q` without paying the pool, and a pool that does not match `q` is exactly what
this read reports on.

Expected pools are built from each trade's returned `total`, never from a
fresh LMSR call: `total` is signed for the trader (negative on a buy,
positive on a sell), so the pool is `seed - sum(totals)`.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives (D-007).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import open_at_zero
from unit_test.trade_fixtures import Upstream, entities, session_factory

# docs/api/ledger-service.md's worked example: b = 100, seed 50, one buy of
# 500 shares of the first outcome, which charges 431.3569.
EXAMPLE_B = Decimal("100.0000")
EXAMPLE_SEED = Decimal("50.0000")
EXAMPLE_QUANTITY = Decimal("500.0000")
EXAMPLE_POOL = Decimal("481.3569")
EXAMPLE_RESIDUE = Decimal("-18.6431")


# --- lazy handles --------------------------------------------------------
def exposure():
    """The `service.exposure` module."""
    from service import exposure  # noqa: PLC0415

    return exposure


async def read_exposure(session: AsyncSession, market_id: uuid.UUID):
    """The interface these tests assume, written down once."""
    return await exposure().exposure_of_market(session, market_id=market_id)


def by_position(outcomes: Iterable) -> dict[int, object]:
    """Outcomes keyed by position, so no assertion depends on list order."""
    return {outcome.position: outcome for outcome in outcomes}


# --- the market ----------------------------------------------------------
async def market_with(
    session: AsyncSession, *, b: Decimal, seed: Decimal, outcomes: int = 2
) -> Upstream:
    """A market opened and funded at this `b` and seed, with `q` at zero.

    `Upstream.__init__` builds `body` fresh per instance, so these writes
    reach no other test's market.
    """
    upstream = Upstream(outcomes=outcomes)
    upstream.body["liquidity_b"] = str(b)
    upstream.body["seed_subsidy"] = str(seed)
    await open_at_zero(session, upstream)
    return upstream


async def book_from_another_session(market_id: uuid.UUID):
    """The book's row as committed, read by a session that is not the test's."""
    book = entities().MarketBook
    async with session_factory()() as other:
        return (
            await other.execute(select(book).where(book.market_id == market_id))
        ).scalar_one()


async def assert_book_terms(
    market_id: uuid.UUID, *, b: Decimal, seed: Decimal
) -> None:
    """The book holds the terms the test asked for, before anything trades.

    Without this, a fixture that silently opened at the default `b` or seed
    would make every expected figure below wrong in a way that still matches.
    """
    book = await book_from_another_session(market_id)
    assert book.liquidity_b == b
    assert book.seed_subsidy == seed


async def row_counts() -> dict[str, int]:
    """Books, accounts and entries, counted by a fresh session, so a commit
    made by the call under test is visible and its own identity map is not."""
    ents = entities()
    counts: dict[str, int] = {}
    async with session_factory()() as other:
        for name, table in (
            ("market_books", ents.MarketBook),
            ("accounts", ents.Account),
            ("entries", ents.Entry),
        ):
            counts[name] = (
                await other.execute(select(func.count()).select_from(table))
            ).scalar_one()
    return counts


def pool_after(seed: Decimal, results: Iterable) -> Decimal:
    """The pool the trades left: the seed, plus every buy's charge, less every
    sell's proceeds. `total` is negative on a buy, so subtracting it adds."""
    pool = seed
    for result in results:
        pool -= result.total
    return pool
