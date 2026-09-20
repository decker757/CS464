"""Two traders touch a market at the same instant. [F-7] #96, D-010, ADR 0015.

The race D-010 is about: two first-touches both find no book, both build one,
and the primary key on `market_id` turns the loser into an `IntegrityError` it
catches, re-reads and proceeds from. Nothing here is simulated — every writer
gets its own session, its own connection and its own database transaction,
because the thing under test is what Postgres does when two transactions want
the same row.

**Every race in this file is gated on an `asyncio.Barrier`, and that is not
decoration.** `asyncio.gather` alone starts coroutines in order and the first
one can be most of the way through its work before the second has opened a
connection — which produces a test that passes because the two never actually
overlapped. `await own.connection()` forces the connection to be established,
then the barrier holds both there until both have arrived. Same shape as
`market_service/unit_test/service/test_approving.py`.

ADR 0015's standard for this file: **a race test is evidence only once it
fails with its lock removed.** Each test below names what to delete to watch it
go red, because a concurrency test nobody has seen fail is a concurrency test
that might be asserting nothing.

`test_concurrency.py` next door covers the same ground for `posting.post` and
`accounts.ensure`. This file is the layer above: the book, the pool account and
the funding transaction, which are three separate writes that have to end up
looking like one.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from model.entities import (
    Account,
    AccountKind,
    Entry,
    Transaction,
    TransactionKind,
)
from unit_test.conftest import mint_token


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

_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")


class _Upstream:
    """A market service shared by every racing caller, counting its calls.

    Thread-safety is not a concern — these are coroutines on one event loop, so
    `self.calls += 1` cannot interleave.
    """

    def __init__(self, market_id: uuid.UUID, outcomes: list[uuid.UUID]) -> None:
        self.calls = 0
        self.market_id = market_id
        self.outcomes = outcomes

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


def _upstream(market_id: uuid.UUID) -> _Upstream:
    return _Upstream(market_id, [uuid.uuid4(), uuid.uuid4()])


async def _race(
    market_id: uuid.UUID, upstream: _Upstream, *, writers: int
) -> list[object]:
    """`writers` first-touches, each in its own transaction, released together.

    Returns what each one produced — a book's `pool_account_id`, or the
    exception it raised — so a caller can assert that they all agreed rather
    than only that one of them worked.
    """
    barrier = asyncio.Barrier(writers)
    factory = get_session_factory()

    async def attempt() -> object:
        async with factory() as own:
            # Connected before anybody proceeds, so the outcome is decided by
            # the database and not by which coroutine had to open a socket.
            await own.connection()
            await barrier.wait()
            try:
                book = await _books().ensure_open(
                    own,
                    market_id,
                    access_token=mint_token(uuid.uuid4()),
                    transport=upstream.transport(),
                )
            except Exception as exc:  # noqa: BLE001 - the assertion is the caller's
                return exc
            return book.pool_account_id

    return list(await asyncio.gather(*(attempt() for _ in range(writers))))


# --- D-010: the book insert race ------------------------------------------
async def test_two_concurrent_first_touches_produce_one_book(
    session: AsyncSession,
) -> None:
    """D-010, and the criterion the ticket words as "one inserts, the other
    catches `IntegrityError`, re-reads and proceeds".

    Both callers find no book. Both build one. The primary key on `market_id`
    admits exactly one, and the loser's recovery is to read the row the winner
    has by then committed — not to fail, because from a trader's side nothing
    went wrong and their preview should simply render.

    **Remove the `except IntegrityError` recovery and this goes red**, with the
    loser surfacing a driver error out of what is, for them, an ordinary first
    look at a market.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    results = await _race(market_id, upstream, writers=2)

    assert all(not isinstance(r, Exception) for r in results), results
    assert len(set(results)) == 1, "both callers must end up on one pool account"

    books_written = (
        await session.execute(
            select(func.count())
            .select_from(_entities().MarketBook)
            .where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()
    assert books_written == 1


async def test_the_loser_gets_the_winner_s_book_rather_than_its_own(
    session: AsyncSession,
) -> None:
    """Re-reads, rather than returning the object it had built in memory.

    The distinction is invisible in this test's return value and enormous
    afterwards: the loser's own `MarketBook` instance was never committed, so a
    caller that carried on using it would hold a `pool_account_id` naming an
    account that does not exist, and the first trade against it would fail a
    foreign key inside the trade's own transaction.

    Six writers rather than two, because the recovery path has to survive
    losing repeatedly, not just once.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    results = await _race(market_id, upstream, writers=6)

    assert all(not isinstance(r, Exception) for r in results), results

    stored = (
        await session.execute(
            select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()

    assert set(results) == {stored.pool_account_id}


async def test_the_race_creates_exactly_one_market_pool_account(
    session: AsyncSession,
) -> None:
    """D-026, and the reason `owner_id` has to be the market id.

    This is the assertion that fails if the pool account is keyed on anything
    else. With a sentinel or a fresh `uuid4`, `uq_accounts_kind_owner` never
    fires, every racing caller commits its own pool account, and only the book
    race is caught — leaving orphan accounts that nothing points at and that
    no later call can find.

    Note what would *not* catch it: the book count, the funding count and the
    ledger sum are all still correct in that world. Only this is wrong.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    await _race(market_id, upstream, writers=6)

    pools = (
        await session.execute(
            select(func.count())
            .select_from(Account)
            .where(
                Account.kind == AccountKind.MARKET_POOL,
                Account.owner_id == market_id,
            )
        )
    ).scalar_one()

    assert pools == 1


async def test_the_pool_is_funded_exactly_once(session: AsyncSession) -> None:
    """The money half of D-010, and the one that would be expensive to get wrong.

    Six simultaneous first touches, one subsidy. The idempotency key
    `market-open:<market_id>` is what makes that true under contention:
    `posting.post` takes the account locks, then looks the key up *under* them,
    so a caller that lost the book race and a caller whose funding raced the
    winner's both find the committed transaction and replay it.

    **Remove the key and this is six times the subsidy**, in a pool the
    administrator approved 250 credits for, with the platform account carrying
    the difference and nothing anywhere reporting it — the whole-ledger sum
    stays zero the entire time, because every leg still balances.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    await _race(market_id, upstream, writers=6)

    funding = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.idempotency_key == _books().market_open_key(market_id))
        )
    ).scalar_one()
    assert funding == 1

    book = (
        await session.execute(
            select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()

    from service import accounts  # noqa: PLC0415

    assert await accounts.balance_of(session, book.pool_account_id) == _SUBSIDY


async def test_the_race_writes_one_set_of_outcome_rows(
    session: AsyncSession,
) -> None:
    """The third write, which has its own uniqueness and its own way to fail.

    `market_books` is protected by its primary key and the funding by its
    idempotency key. `market_outcomes` has neither — it is a child collection,
    and its guards are the two unique constraints the ticket specifies, on
    `(market_id, outcome_id)` and on `(market_id, position)`.

    Without them the losing callers' rows land beside the winner's, and the
    first price calculation reads four outcomes in a binary market. `q` would
    be zero for all of them, so the prices would still sum to one — at 0.25
    each, on a market with two outcomes.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    await _race(market_id, upstream, writers=6)

    rows = list(
        (
            await session.execute(
                select(_entities().MarketOutcome)
                .where(_entities().MarketOutcome.market_id == market_id)
                .order_by(_entities().MarketOutcome.position)
            )
        ).scalars()
    )

    assert [r.outcome_id for r in rows] == upstream.outcomes
    assert [r.position for r in rows] == [0, 1]


async def test_only_the_callers_that_raced_the_first_touch_fetch_the_terms(
    session: AsyncSession,
) -> None:
    """The HTTP cost of the race is bounded by the race, not by the traffic.

    Every caller that arrives before the winner commits legitimately fetches —
    none of them can see a book that does not exist yet, and that is the
    window D-008 accepted. What must not happen is a fetch *after* the book
    exists, which is the fast path `test_market_books.py` pins.

    So: at most one call per racing writer, and exactly zero afterwards.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    await _race(market_id, upstream, writers=4)
    during_the_race = upstream.calls

    assert 1 <= during_the_race <= 4

    await _books().ensure_open(
        session,
        market_id,
        access_token=mint_token(uuid.uuid4()),
        transport=upstream.transport(),
    )

    assert upstream.calls == during_the_race


# --- the invariant, under contention --------------------------------------
async def test_the_ledger_still_balances_after_a_race(session: AsyncSession) -> None:
    """Every entry ever written, summed, after the messiest path in the ticket.

    Six callers, three of them opening different markets, all against one
    platform account — which is the shape where a dropped leg or a half-written
    transaction would actually show up.
    """
    for _ in range(3):
        market_id = uuid.uuid4()
        await _race(market_id, _upstream(market_id), writers=6)

    # Asserted before the sum, because an empty table also sums to zero. Without
    # this the test passes against a service that opened no books at all, which
    # is exactly the state it is in before the implementation exists.
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


async def test_every_transaction_balances_on_its_own_after_a_race(
    session: AsyncSession,
) -> None:
    """The per-transaction version, because a whole-table zero survives two
    mistakes that cancelled — and three markets funded from one platform
    account is exactly where two such mistakes would find each other."""
    for _ in range(3):
        market_id = uuid.uuid4()
        await _race(market_id, _upstream(market_id), writers=6)

    # Same guard as above: no rows means no unbalanced rows.
    written = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert written == 6, "three fundings of two legs each"

    unbalanced = (
        await session.execute(
            select(Entry.transaction_id)
            .group_by(Entry.transaction_id)
            .having(func.sum(Entry.amount) != ZERO)
        )
    ).scalars()

    assert list(unbalanced) == []


async def test_two_markets_opening_at_once_do_not_deadlock(
    session: AsyncSession,
) -> None:
    """ADR 0015's lock-ordering rule, at the point where it first has two locks.

    Every market's funding touches the *same* platform account, so two markets
    opening simultaneously contend on one row while each also holds its own
    pool account. `accounts.lock` takes account locks ascending by id, which is
    an order both callers agree on without coordinating — and a market id sorts
    unpredictably against the all-zero platform owner, so a path that locked in
    the order its legs happened to be listed would deadlock here roughly half
    the time.

    A deadlock shows up as this test hanging rather than failing, so it is
    bounded: `asyncio.timeout` turns it into a failure with a name.
    """
    first, second = uuid.uuid4(), uuid.uuid4()

    async with asyncio.timeout(30):
        await asyncio.gather(
            _race(first, _upstream(first), writers=3),
            _race(second, _upstream(second), writers=3),
        )

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
