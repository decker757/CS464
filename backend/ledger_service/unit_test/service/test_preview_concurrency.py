"""Two traders typing a quantity at the same instant. [T-1] #21, D-012, D-036

Every party has its own session and transaction, and every race waits on an
`asyncio.Barrier` after connecting (ADR 0015). The first test fails when a lock
is *added*: the only honest way to assert D-012's absence of one. Each test
names what to change to watch it go red.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from model.entities import Account, AccountKind, Entry, Transaction, TransactionKind
from unit_test.conftest import mint_token, terms_client_over


def _preview():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import preview  # noqa: PLC0415

    return preview


def _pricing():
    from core import pricing  # noqa: PLC0415

    return pricing


def _books():
    from service import books  # noqa: PLC0415

    return books


def _entities():
    from model import entities  # noqa: PLC0415

    return entities


ZERO = Decimal(0)

_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")
_Q = [Decimal("137.5000"), Decimal("42.2500")]
_QUANTITY = Decimal("10.0000")

# A ceiling, not a duration. Nothing here should take anywhere near this long;
# it exists so that a lock that serialises the previews shows up as a named
# failure rather than as a suite that hangs until CI gives up.
_TIMEOUT = 30


class _Upstream:
    """One market service shared by every racing caller, counting its calls.

    Thread-safety is not a concern — these are coroutines on one event loop, so
    `self.calls += 1` cannot interleave.
    """

    def __init__(self, *, outcomes: int = 2) -> None:
        self.calls = 0
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(
                200,
                json={
                    "id": str(self.market_id),
                    "status": "open",
                    "close_time": "2027-01-05T12:00:00Z",
                    "resolution_time": "2027-01-20T12:00:00Z",
                    "liquidity_b": str(_B),
                    "seed_subsidy": str(_SUBSIDY),
                    "published_at": "2026-09-01T09:00:00Z",
                    "outcomes": [
                        {"id": str(o), "position": i, "label": f"Outcome {i}"}
                        for i, o in enumerate(self.outcomes)
                    ],
                },
            )

        return httpx.MockTransport(handler)


async def _warm(session: AsyncSession, upstream: _Upstream) -> None:
    """A book that exists and outcomes that hold shares, committed.

    Committed matters: the racing sessions below are separate transactions and
    under READ COMMITTED they see only what has landed.
    """
    await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=mint_token(uuid.uuid4()),
        terms_client=terms_client_over(upstream.transport()),
    )
    outcome = _entities().MarketOutcome
    for position, value in enumerate(_Q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == upstream.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


async def _quote(session: AsyncSession, upstream: _Upstream, *, outcome: int = 0):
    return await _preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[outcome],
        side=_pricing().Side.BUY,
        quantity=_QUANTITY,
        access_token=mint_token(uuid.uuid4()),
        terms_client=terms_client_over(upstream.transport()),
    )


# --- D-012: the pricing read does not serialise ---------------------------
async def test_two_concurrent_previews_of_a_warm_market_do_not_serialise(
    session: AsyncSession,
) -> None:
    """D-012, built as the situation a lock would hang in: both parties price
    and then hold their transactions open until both have a quote.

    Add `.with_for_update()` to the pricing read and this times out.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    start = asyncio.Barrier(2)
    done = asyncio.Barrier(2)
    factory = get_session_factory()

    async def attempt():
        async with factory() as own:
            # Connected before anybody proceeds, so the outcome is decided by
            # the database and not by which coroutine had to open a socket.
            await own.connection()
            await start.wait()
            quote = await _quote(own, upstream)
            # The transaction is deliberately still open here. Whatever locks
            # the read took, if any, are still held, so the other party cannot
            # have got past them.
            await done.wait()
            await own.rollback()
            return quote

    async with asyncio.timeout(_TIMEOUT):
        first, second = await asyncio.gather(attempt(), attempt())

    assert first.total == second.total
    assert first.state_version == second.state_version


async def test_concurrent_previews_of_one_market_agree(
    session: AsyncSession,
) -> None:
    """Same committed state, same answer, whatever the interleaving: a
    disagreement would mean a partial snapshot (D-013). Eight parties.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    start = asyncio.Barrier(8)
    factory = get_session_factory()

    async def attempt():
        async with factory() as own:
            await own.connection()
            await start.wait()
            quote = await _quote(own, upstream)
            await own.rollback()
            return (quote.total, quote.average_price, quote.state_version)

    async with asyncio.timeout(_TIMEOUT):
        results = await asyncio.gather(*(attempt() for _ in range(8)))

    assert len(set(results)) == 1, results


async def test_a_race_of_warm_previews_writes_nothing(
    session: AsyncSession,
) -> None:
    """The read-only claim under eight simultaneous callers, where a narrow
    "create it if missing" window would fire."""
    upstream = _Upstream()
    await _warm(session, upstream)

    before = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()

    start = asyncio.Barrier(8)
    factory = get_session_factory()

    async def attempt():
        async with factory() as own:
            await own.connection()
            await start.wait()
            await _quote(own, upstream)
            await own.rollback()

    async with asyncio.timeout(_TIMEOUT):
        await asyncio.gather(*(attempt() for _ in range(8)))

    session.expire_all()
    after = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert after == before

    book = _entities().MarketBook
    assert (
        await session.execute(
            select(book.state_version).where(book.market_id == upstream.market_id)
        )
    ).scalar_one() == 0


# --- D-010 and D-037: the first touch, reached through the preview --------
async def test_concurrent_first_previews_open_exactly_one_book(
    session: AsyncSession,
) -> None:
    """D-010's race through the preview (D-037): every caller gets a quote,
    and one book, pool and subsidy exist.

    Remove the `except IntegrityError` recovery in `books.ensure_open` and this
    goes red.
    """
    upstream = _Upstream()

    start = asyncio.Barrier(6)
    factory = get_session_factory()

    async def attempt():
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                quote = await _quote(own, upstream)
            except Exception as exc:  # noqa: BLE001 - the assertion is the caller's
                return exc
            return quote.total

    async with asyncio.timeout(_TIMEOUT):
        results = await asyncio.gather(*(attempt() for _ in range(6)))

    assert all(not isinstance(r, Exception) for r in results), results
    assert len(set(results)) == 1, "one market, one q, one answer"

    book = _entities().MarketBook
    assert (
        await session.execute(
            select(func.count())
            .select_from(book)
            .where(book.market_id == upstream.market_id)
        )
    ).scalar_one() == 1

    assert (
        await session.execute(
            select(func.count())
            .select_from(Account)
            .where(
                Account.kind == AccountKind.MARKET_POOL,
                Account.owner_id == upstream.market_id,
            )
        )
    ).scalar_one() == 1

    assert (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.idempotency_key
                == _books().market_open_key(upstream.market_id)
            )
        )
    ).scalar_one() == 1


async def test_the_ledger_still_balances_after_a_race_of_first_previews(
    session: AsyncSession,
) -> None:
    """Three markets opened at once by preview from one `PLATFORM` account:
    the ledger sums to zero, and so does each transaction (ADR 0009).
    """
    upstreams = [_Upstream() for _ in range(3)]

    async def race(upstream: _Upstream):
        start = asyncio.Barrier(4)
        factory = get_session_factory()

        async def attempt():
            async with factory() as own:
                await own.connection()
                await start.wait()
                await _quote(own, upstream)

        await asyncio.gather(*(attempt() for _ in range(4)))

    async with asyncio.timeout(_TIMEOUT):
        await asyncio.gather(*(race(u) for u in upstreams))

    funded = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.kind == TransactionKind.MARKET_SEED)
        )
    ).scalar_one()
    assert funded == 3, "three markets must have been funded for this to mean anything"

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO

    unbalanced = (
        await session.execute(
            select(Entry.transaction_id)
            .group_by(Entry.transaction_id)
            .having(func.sum(Entry.amount) != ZERO)
        )
    ).scalars()
    assert list(unbalanced) == []


async def test_two_markets_opening_by_preview_at_once_do_not_deadlock(
    session: AsyncSession,
) -> None:
    """Two markets opening at once both finish, inside a timeout.

    Not evidence for ADR 0015's lock order: the two share only the platform
    row, so they cannot deadlock however `accounts.lock` sorts. What it holds
    is that the shared row serialises rather than stalls.
    """
    first, second = _Upstream(), _Upstream()

    async def race(upstream: _Upstream):
        start = asyncio.Barrier(3)
        factory = get_session_factory()

        async def attempt():
            async with factory() as own:
                await own.connection()
                await start.wait()
                await _quote(own, upstream)

        await asyncio.gather(*(attempt() for _ in range(3)))

    async with asyncio.timeout(_TIMEOUT):
        await asyncio.gather(race(first), race(second))

    funded = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.kind == TransactionKind.MARKET_SEED)
        )
    ).scalar_one()
    assert funded == 2

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO
