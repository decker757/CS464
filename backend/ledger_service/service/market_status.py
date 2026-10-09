"""The stopped-market gate: may this market still be traded? [F-8] #109, ADR 0017.

The book holds no status or `close_time` and cannot, so this asks
market_service, whose `status` already answers both the clock's close and an
administrator's (ADR 0011). The replay-before-gate and gate-before-lock order
belongs to the trade path. `ensure_trading` returns `None`, never `MarketTerms`:
fresh terms there are one refactor from pricing off the wire instead of the
book (ADR 0005).
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import has_pending_writes
from core.errors import MarketClosed, MarketNotFound, MarketTermsUnavailable
from service import books, market_terms
from service.market_terms import MarketTerms

# The one status a trade may proceed against, restated rather than imported
# (see `books.MIN_OUTCOMES`). A wrong value would refuse every trade silently,
# so `test_market_status.py` pins it against market_service's source.
_OPEN = "open"


async def read_terms(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> MarketTerms:
    """The market's terms from market_service, read holding no connection.

    A 404 is `MarketTermsUnavailable` if the market has a book (market_service
    is wrong) and `MarketNotFound` if not (ADR 0017). That `books.find` is
    unlocked: ADR 0015 locks reads that decide a write, and this one decides
    only an error code. Every other upstream failure propagates from
    `market_terms.fetch`, so a failing dependency always surfaces as its own
    error.

    **Call this with nothing pending and no row lock held.** It rolls back
    before the HTTP call, as `books.ensure_open` does (D-043), so a lock taken
    before it would be released mid-request, which ADR 0015 forbids. The
    assert below catches a pending write and cannot see a `SELECT ... FOR
    UPDATE`. The rollback also expires every ORM instance the session has
    loaded.
    """
    # Enforced, not just documented: the rollback would discard a pending
    # write silently. A held lock is the caller's to avoid; see above.
    assert not has_pending_writes(session), (
        "read_terms() rolls back before it calls market_service: call it "
        "with nothing pending on the session"
    )

    # D-043: release the connection a read before this autobegan, so the call
    # (up to 5s) holds none. The `books.find` below autobegins its own.
    await session.rollback()
    try:
        return await market_terms.fetch(
            market_id, access_token=access_token, terms_client=terms_client
        )
    except MarketNotFound as not_found:
        book = await books.find(session, market_id)
        if book is not None:
            raise MarketTermsUnavailable from not_found
        raise


async def ensure_trading(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> None:
    """Raise if this market is not open for trading. Otherwise return `None`.

    Raises `MarketClosed` for any status but "open", and whatever `read_terms`
    raises. **Call this with nothing pending and no row lock held**: the gate
    runs before the book lock, and `read_terms` rolls back before it calls out.
    """
    terms = await read_terms(
        session, market_id, access_token=access_token, terms_client=terms_client
    )

    if terms.status != _OPEN:
        raise MarketClosed
