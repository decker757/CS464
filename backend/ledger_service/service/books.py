"""Opening a market's book, and funding its pool. [F-7] #96, D-008, D-009, D-010.

The ledger has never heard of a market. ADR 0005 has `b` and the subsidy
crossing at publish as an immutable snapshot, and that handoff was never
built. D-008 resolves it as a lazy pull on first touch, the same shape as the
signup grant in `service/grants.py`: the first request that needs a market's
book finds none, reads the terms from market_service's public detail
endpoint, and creates the book — no event, no outbox, and no window in which
the state is observably wrong, because the read that would observe the window
is the read that closes it.

**The commit boundary.** `posting.post` commits internally, and nothing here
commits before it. `accounts.ensure` and the book/outcomes insert below each
use `session.begin_nested()` — a SAVEPOINT within the still-open outer
transaction — so nothing they write is durable on its own. The one
`session.commit()` in this whole path is the one already inside `posting.post`,
called last, and it commits everything pending in the transaction at once: the
pool account, the book, its outcomes, and the funding transaction's entries,
together. Raise before reaching it — `MarketNotPublished`,
`MarketTermsUnavailable`, or an uncaught `IntegrityError` — and nothing in this
function has been committed, which is what `test_a_refused_market_leaves_nothing_behind`
and `test_an_unreachable_market_service_leaves_nothing_behind` pin. Nothing in
`service/posting.py` changed to make this work; the ordering is the answer.

The first-touch race (D-010) is handled the level it has to be: two concurrent
callers both find no book and both attempt to insert one, and the primary key
on `market_id` turns the loser's attempt into an `IntegrityError`, caught here,
after which the loser re-reads the row the winner committed and carries on
with it rather than with the object it built in memory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotPublished
from model.entities import AccountKind, MarketBook, MarketOutcome, TransactionKind
from service import accounts, market_terms, posting
from service.posting import Leg

# Namespaced like every other idempotency key this system generates, so that
# `market-open:<market_id>` cannot collide with `signup-grant:<user_id>` even
# on the (never observed) day a market id and a user id are byte-identical.
_KEY_PREFIX = "market-open"


def market_open_key(market_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{market_id}"


async def ensure_open(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.BaseTransport | None = None,
) -> MarketBook:
    """This market's book, opening and funding it first if nobody has yet.

    The fast path is the whole point: an existing book is a plain read, with
    no HTTP call and no idempotency lookup, so a market that has already been
    touched once trades without depending on market_service being reachable
    at all.

    `access_token` is the caller's own — forwarded to market_service exactly
    as D-018's Notes describe, and never minted here.
    """
    book = await _find(session, market_id)
    if book is not None:
        return book

    terms = await market_terms.fetch(
        market_id, access_token=access_token, transport=transport
    )
    if terms.published_at is None:
        raise MarketNotPublished

    # D-028: keyed on the market id, so the insert race below and this one
    # fail the same way for the same reason. `accounts.ensure` recovers from
    # its own race internally; by the time it returns here, every racing
    # caller holds the one pool account that exists for this market.
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
        # D-010. The loser re-reads rather than keeps the object it built:
        # that object was never committed, so its pool_account_id would name
        # an account the caller could go on to use while nothing else agrees
        # it exists.
        existing = await _find(session, market_id)
        if existing is None:
            raise
        book = existing

    platform = await accounts.ensure_platform(session)

    # D-009. Idempotent on market_open_key: the winner's funding transaction
    # replays for every loser and for every later touch, so a market is
    # funded exactly once regardless of how many callers raced to open it.
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


async def _find(session: AsyncSession, market_id: uuid.UUID) -> MarketBook | None:
    stmt = select(MarketBook).where(MarketBook.market_id == market_id)
    return (await session.execute(stmt)).scalar_one_or_none()
