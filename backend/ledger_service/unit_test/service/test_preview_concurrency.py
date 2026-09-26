"""Two traders typing a quantity at the same instant. [T-1] #21, D-012, D-036

Nothing here is simulated. Every party gets its own session, its own connection
and its own database transaction, because the thing under test is what Postgres
does when two transactions want the same rows — and, for the first test, what
it does when they do *not*.

**Every race is gated on an `asyncio.Barrier`, and that is not decoration.**
`asyncio.gather` alone starts coroutines in order and the first can be most of
the way through its work before the second has opened a connection, which
produces a test that passes because the two never overlapped. `await
own.connection()` forces the connection to exist, then the barrier holds both
there until both have arrived. ADR 0015 makes that the standard for any test in
this repository asserting something about two transactions.

**The first test is the unusual one: it fails when a lock is *added*.** Every
other race test in this suite fails when a lock is removed. D-012's claim is
that the pricing read takes none, and the only honest way to assert an absence
is to build the situation a lock would deadlock in and show that it does not.
Each test below names what to change to watch it go red.
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
from unit_test.conftest import mint_token


def _preview():
    """Imported inside each test rather than at module scope.

    `service/preview.py` does not exist yet, and a top-level import would be
    one collection error taking the whole file down as a single red line.
    Reached through here, every test fails on its own (D-007).
    """
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
        transport=upstream.transport(),
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
        transport=upstream.transport(),
    )


# --- D-012: the pricing read does not serialise ---------------------------
async def test_two_concurrent_previews_of_a_warm_market_do_not_serialise(
    session: AsyncSession,
) -> None:
    """D-012's actual claim, built as the situation a lock would hang in.

    Both parties connect, meet at the barrier, price the same market, and then
    **hold their transactions open** at a second barrier until both have
    finished. Neither may proceed until the other has a quote in hand.

    If the pricing read took `with_for_update()` on the book row — or on the
    outcome rows — the second party would block inside `quote()` waiting for
    the first party's transaction to end, and the first party would be waiting
    at `done` for the second. Nothing would complete and `asyncio.timeout`
    turns that into a failure with a name instead of a suite that hangs.

    **Add `.with_for_update()` to the pricing read and this goes red**, which
    is the reverse of every other race test in this suite and is the only way
    to assert an absence honestly. D-012's cost argument is the reason it
    matters: a preview fires on every keystroke, so a lock here queues every
    trader typing a quantity into the same market behind one another.
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
    """Same committed state, same answer, whatever the interleaving.

    The preview is a pure function of `b`, `q` and the request, all read under
    one statement (D-013). Two callers against an unchanged market must
    therefore agree exactly — not approximately — and a disagreement would mean
    one of them read a partial snapshot, which is the failure D-013's single
    statement exists to rule out.

    Eight parties rather than two, because a snapshot bug that needs a
    particular interleaving will not show up in one pair.
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
    """The read-only claim, under contention rather than in isolation.

    `test_preview.py` proves a single warm preview writes nothing. This proves
    the same under eight simultaneous callers, where a "create it if it is
    missing" path with a narrow window would finally fire — the shape that
    makes a bug appear only in production, on the busiest market, once.
    """
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
    """D-010's race, arriving through the caller D-037 says owns it.

    `test_book_concurrency.py` proves `books.ensure_open` survives six
    simultaneous first touches. This proves the preview inherits that rather
    than wrapping it in a check of its own: every caller comes back with a
    quote, one book exists, one pool account exists, and the subsidy was posted
    once.

    **Remove the `except IntegrityError` recovery in `books.ensure_open` and
    this goes red**, with losing traders seeing a driver error on what is, for
    them, an ordinary first look at a market.
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
    """Every entry ever written, summed, after the only path in this ticket
    that moves money — under the contention D-036 records.

    Three markets opened simultaneously by preview, all funded from the one
    `PLATFORM` account, which is the shape where a dropped leg or a
    double-funded pool would actually show up. The per-transaction check is
    beside it because a whole-table zero survives two mistakes that cancelled,
    and three pools funded from one house account is exactly where two such
    mistakes would find each other.
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
    """Two markets opening at once finish, rather than waiting on each other.

    **This does not prove ADR 0015's lock ordering, and it used to claim it
    did.** Every funding transaction touches the same `PLATFORM` row and its
    own pool, so the two callers here want `{platform, pool_a}` and
    `{platform, pool_b}` — one row in common. A cycle needs two transactions
    wanting the *same two* rows in opposite orders, so this pair cannot
    deadlock however `accounts.lock` sorts. `books.ensure_open` also lists its
    legs `[platform, pool]` every time, so even unsorted both callers agree.
    Delete the `sorted()` in `accounts.lock` and this test stays green, which
    is the definition of a test that is not evidence.

    What it does hold is worth holding: contention on the shared platform row
    serialises rather than stalls, and the pool account race resolves, for
    every caller, inside the timeout. Testing the ordering rule itself needs
    two transactions that each take two pool rows, which nothing in this
    service does yet — [T-2] #22 is the first writer that could.

    A deadlock shows up as a hang rather than a failure, so it is bounded.
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
