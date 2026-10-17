"""Two traders touch a market at the same instant. [F-7] #96, D-010, ADR 0015.

Every writer has its own session, connection and transaction, and every race
waits on an `asyncio.Barrier` after connecting, or the writers never overlap.
A race test is evidence only once it fails with its guard removed (ADR 0015),
so each test names what to delete to watch it go red.
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
from unit_test.conftest import mint_token, terms_client_over


def _books():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
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

    def __init__(
        self,
        market_id: uuid.UUID,
        outcomes: list[uuid.UUID],
        subsidies: list[Decimal] | None = None,
    ) -> None:
        self.calls = 0
        self.market_id = market_id
        self.outcomes = outcomes
        # One subsidy per call, last value repeating. Unreachable for a real
        # market service, and the only way to tell funding from the committed
        # book apart from funding from the caller's own copy of the terms.
        self.subsidies = subsidies or [_SUBSIDY]

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            subsidy = self.subsidies[min(self.calls, len(self.subsidies) - 1)]
            self.calls += 1
            return httpx.Response(
                200,
                json={
                    "id": str(self.market_id),
                    "status": "open",
                    "close_time": "2027-01-05T12:00:00Z",
                    "resolution_time": "2027-01-20T12:00:00Z",
                    "liquidity_b": str(_B),
                    "seed_subsidy": str(subsidy),
                    "published_at": "2026-09-01T09:00:00Z",
                    "proposed_outcome_id": None,
                    "settleable": False,
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

    Returns the `MarketBook` each ended up holding, or the exception it raised.
    The whole book, because only `opened_at` tells the winner's from a loser's
    own. Readable after the session closes: `expire_on_commit=False`.
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
                    terms_client=terms_client_over(upstream.transport()),
                )
            except Exception as exc:  # noqa: BLE001 - the assertion is the caller's
                return exc
            return book

    return list(await asyncio.gather(*(attempt() for _ in range(writers))))


# --- D-010: the book insert race ------------------------------------------
async def test_two_concurrent_first_touches_produce_one_book(
    session: AsyncSession,
) -> None:
    """D-010: "one inserts, the other catches `IntegrityError`, re-reads and
    proceeds".

    Remove the `except IntegrityError` recovery and this goes red.
    """
    market_id = uuid.uuid4()
    upstream = _upstream(market_id)

    results = await _race(market_id, upstream, writers=2)

    assert all(not isinstance(r, Exception) for r in results), results
    assert len({r.pool_account_id for r in results}) == 1, (
        "both callers must end up on one pool account"
    )

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
    """Re-reads, rather than returning the uncommitted object it built.

    Compared on `opened_at`, which each caller stamps itself; every racer
    shares one pool account, so `pool_account_id` cannot tell. Remove
    `book = existing` from `service/books.py` and this goes red.
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

    assert {r.opened_at for r in results} == {stored.opened_at}, (
        "every caller must end up holding the committed book, not its own"
    )
    assert {r.pool_account_id for r in results} == {stored.pool_account_id}


async def test_the_loser_funds_from_the_committed_book_not_its_own_terms(
    session: AsyncSession,
) -> None:
    """`Leg(amount=book.seed_subsidy)`, never `terms.seed_subsidy`.

    A loser funding from its own copy would send different legs under the same
    key and get `IdempotencyKeyReused`. Needs an upstream answering differently
    per call, or the two numbers are equal and the mistake invisible.
    """
    market_id = uuid.uuid4()
    upstream = _Upstream(
        market_id,
        [uuid.uuid4(), uuid.uuid4()],
        subsidies=[_SUBSIDY, Decimal("999.0000")],
    )

    results = await _race(market_id, upstream, writers=2)

    assert all(not isinstance(r, Exception) for r in results), results

    stored = (
        await session.execute(
            select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
        )
    ).scalar_one()

    funded = (
        await session.execute(
            select(func.coalesce(func.sum(Entry.amount), ZERO)).where(
                Entry.account_id == stored.pool_account_id
            )
        )
    ).scalar_one()

    assert funded == stored.seed_subsidy, (
        "the pool holds a different amount from the one the book records"
    )


async def test_the_race_creates_exactly_one_market_pool_account(
    session: AsyncSession,
) -> None:
    """D-028: keyed on anything but the market id, every racer commits its own
    pool account, and only this assertion notices.
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
    """The money half of D-010: six first touches, one subsidy, because the
    `market-open` key replays under the account locks.

    Remove the key and this is six subsidies, with the ledger still summing to
    zero.
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
    """The third write, guarded by the two unique constraints on
    `market_outcomes`, or a binary market reads as four outcomes.
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
    """The HTTP cost is bounded by the race: at most one call per racing
    writer (D-008), and none once the book exists.
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
        terms_client=terms_client_over(upstream.transport()),
    )

    assert upstream.calls == during_the_race


# --- the invariant, under contention --------------------------------------
async def test_the_ledger_still_balances_after_a_race(session: AsyncSession) -> None:
    """Every entry ever written, summed, after six racing callers on three
    markets and one platform account."""
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
    """Two markets opening at once share the platform account and must both
    finish. It shares one row, so it cannot prove the ascending lock order
    (see `test_preview_concurrency.py`). `asyncio.timeout` names a hang.
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
