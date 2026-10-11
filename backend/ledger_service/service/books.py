"""Opening a market's book, and funding its pool. [F-7] #96, D-008, D-009, D-010.

The first request that needs a book finds none, reads the terms from
market_service, and creates it: a lazy pull on first touch (D-008). Every write
before `posting.post` sits in a SAVEPOINT, and `post`'s commit is the only one,
so the pool account, book, outcomes and funding land together or not at all
(D-032). A concurrent first touch loses on the `market_id` primary key and
re-reads the winner's book (D-010).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import has_pending_writes
from core.errors import MarketNotPublished, MarketTermsUnavailable
from model.entities import (
    AccountKind,
    MarketBook,
    MarketOutcome,
    MarketResult,
    TransactionKind,
)
from service import accounts, market_terms, posting
from service.market_terms import OutcomeTerms
from service.posting import Leg

# The fewest outcomes a market can be priced with; `service/book_prices.py`
# holds the same floor on the way out. Restated from market_service's
# `validation.MIN_OUTCOMES`, not imported: `test_import_boundary.py` forbids
# that import, which resolves under pytest and fails in the container.
MIN_OUTCOMES = 2

# Namespaced so it cannot collide with `signup-grant:<user_id>`.
_KEY_PREFIX = "market-open"


def _refuse_unpriceable(outcomes: list[OutcomeTerms]) -> None:
    """Refuse an outcome list that could be stored but never priced. ADR 0017.

    A rule about writing an immutable book (ADR 0005), unreachable through a
    correct market_service. Fewer than two outcomes cannot be traded against.
    A repeated id or position would fail a unique constraint inside the
    savepoint and be mistaken for a lost first-touch race: a 500, not a 503.
    """
    if len(outcomes) < MIN_OUTCOMES:
        raise MarketTermsUnavailable
    # Non-negative: the column has no CHECK, but `model/schemas.py` declares
    # `ge=0`, so a negative one would commit and then fail serialisation on
    # every later read of that book, a permanent 500.
    if any(o.position < 0 for o in outcomes):
        raise MarketTermsUnavailable
    if len({o.outcome_id for o in outcomes}) != len(outcomes):
        raise MarketTermsUnavailable
    if len({o.position for o in outcomes}) != len(outcomes):
        raise MarketTermsUnavailable


def is_positive_finite(value: Decimal) -> bool:
    """True for a finite value above zero: the only `b` or subsidy a book can
    be written or priced from.

    Finiteness first: `Decimal` takes "Infinity" and "NaN" off the wire, and
    `NaN > 0` raises. `numeric` stores NaN, so a stored `b` can be one too.
    """
    return value.is_finite() and value > 0


async def find(session: AsyncSession, market_id: uuid.UUID) -> MarketBook | None:
    """This market's book, or None. Unlocked."""
    stmt = select(MarketBook).where(MarketBook.market_id == market_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def market_open_key(market_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{market_id}"


async def ensure_open(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> MarketBook:
    """This market's book, opening and funding it first if nobody has yet.

    An existing book is a plain read with no HTTP call, so a touched market
    trades without market_service. `access_token` is the caller's own,
    forwarded and never minted (D-018). The cold path commits, and raises
    `MarketNotPublished`, `MarketTermsUnavailable` or what `market_terms.fetch`
    raises.

    **Call this with nothing pending on the session**: the cold path rolls
    back before the terms pull (D-043).
    """
    # Enforced, not just documented: the rollback would discard a pending
    # write silently, and release a lock taken before this call mid-request,
    # which ADR 0015 forbids.
    assert not has_pending_writes(session), (
        "ensure_open() rolls back on the cold path: call it with nothing "
        "pending on the session, or hoist the terms fetch out of it"
    )

    book = await find(session, market_id)
    if book is not None:
        return book

    # D-043: release the connection the empty read checked out, so the terms
    # pull (up to 5s) holds none. Nothing is lost: the read wrote nothing.
    await session.rollback()

    terms = await market_terms.fetch(
        market_id, access_token=access_token, terms_client=terms_client
    )
    if terms.published_at is None:
        raise MarketNotPublished

    # ADR 0017: refused before the insert. A null would raise `IntegrityError`
    # inside the savepoint, where the handler means only a lost first-touch
    # race (D-010), and end as a 500 instead of this 503.
    if terms.liquidity_b is None or terms.seed_subsidy is None:
        raise MarketTermsUnavailable
    # The book is immutable (ADR 0005), so a bad `b` is a bare `ValueError`
    # from `core/lmsr.py` on every price, forever. A zero subsidy builds zero
    # legs `post` refuses as a 422; a negative one drains the pool, and no
    # overdraft check covers a pool. An infinite one fails the column as an
    # unmapped `DataError`. market_service checks both; this guards the write.
    if not is_positive_finite(terms.liquidity_b):
        raise MarketTermsUnavailable
    if not is_positive_finite(terms.seed_subsidy):
        raise MarketTermsUnavailable
    _refuse_unpriceable(terms.outcomes)

    # D-028: keyed on the market id, so every racing caller ends up holding
    # the one pool account for this market.
    pool = await accounts.ensure(session, AccountKind.MARKET_POOL, market_id)

    now = datetime.now(UTC)
    book = MarketBook(
        market_id=market_id,
        liquidity_b=terms.liquidity_b,
        seed_subsidy=terms.seed_subsidy,
        pool_account_id=pool.id,
        state_version=0,
        # D-029: equal to opened_at. For a market nobody has traded, creating
        # this row is the last state change there has been.
        state_changed_at=now,
        opened_at=now,
    )

    try:
        async with session.begin_nested():
            session.add(book)
            session.add_all(
                MarketOutcome(
                    market_id=market_id,
                    outcome_id=outcome.outcome_id,
                    position=outcome.position,
                )
                for outcome in terms.outcomes
            )
            await session.flush()
    except IntegrityError:
        # D-010. The loser re-reads rather than keep the uncommitted object
        # it built.
        existing = await find(session, market_id)
        if existing is None:
            raise
        book = existing

    platform = await accounts.ensure_platform(session)

    # D-009. Idempotent on `market_open_key`, so racing callers replay the
    # winner's funding and a market is funded once.
    await posting.post(
        session,
        idempotency_key=market_open_key(market_id),
        kind=TransactionKind.MARKET_SEED,
        legs=[
            Leg(account=platform, amount=-book.seed_subsidy),
            Leg(account=pool, amount=book.seed_subsidy),
        ],
        context={"market_id": str(market_id)},
        now=now,
    )

    return book


async def lock_book(session: AsyncSession, market_id: uuid.UUID) -> MarketBook:
    """This market's book, row-locked. Raises `NoResultFound` if it has none.

    "Lock order: book row before account rows": the wider lock, taken before
    any account lock, on every path that writes a market's money.
    """
    stmt = select(MarketBook).where(MarketBook.market_id == market_id).with_for_update()
    return (await session.execute(stmt)).scalar_one()


async def find_result(
    session: AsyncSession, market_id: uuid.UUID
) -> MarketResult | None:
    """How this market ended, or None if it has not. Takes no lock of its own.

    Any row, never filtered on `kind`: a row is the latch for every ending
    (ADR 0019, as amended 2026-10-09). A real SELECT, never `session.get`,
    whose identity map would answer from a row loaded before the book lock.
    """
    stmt = select(MarketResult).where(MarketResult.market_id == market_id)
    return (await session.execute(stmt)).scalar_one_or_none()
