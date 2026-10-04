"""The stopped-market gate: may this market still be traded? [F-8] #109, ADR 0017.

The book holds no status or `close_time` and cannot, so this asks
market_service, whose `status` already answers both the clock's close and an
administrator's (ADR 0011). The replay-before-gate and gate-before-lock order
belongs to the trade path. Returns `None`, never `MarketTerms`: fresh terms
here are one refactor from pricing off the wire instead of the book (ADR 0005).
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import has_pending_writes
from core.errors import MarketClosed, MarketNotFound, MarketTermsUnavailable
from service import books, market_terms

# The one status a trade may proceed against, restated rather than imported
# (see `books.MIN_OUTCOMES`). A wrong value would refuse every trade silently,
# so `test_market_status.py` pins it against market_service's source.
_OPEN = "open"


async def ensure_trading(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> None:
    """Raise if this market is not open for trading. Otherwise return `None`.

    Raises `MarketClosed` for any status but "open". A 404 is
    `MarketTermsUnavailable` if the market has a book (market_service is
    wrong) and `MarketNotFound` if not (ADR 0017). That `books.find` is
    unlocked: ADR 0015 locks reads that decide a write, and this one decides
    only an error code. Every other upstream failure propagates from
    `market_terms.fetch`, so a sick dependency is never read as closed.

    **Call this with nothing pending on the session**: it rolls back before
    the HTTP call, as `books.ensure_open` does (D-043), which also releases
    any lock taken before it.
    """
    # Enforced, not just documented: the rollback would discard a pending
    # write silently.
    assert not has_pending_writes(session), (
        "ensure_trading() rolls back before it calls market_service: call it "
        "with nothing pending on the session"
    )

    # D-043: release the connection a read before this autobegan, so the call
    # (up to 5s) holds none. The `books.find` below autobegins its own.
    await session.rollback()
    try:
        terms = await market_terms.fetch(
            market_id, access_token=access_token, terms_client=terms_client
        )
    except MarketNotFound as not_found:
        book = await books.find(session, market_id)
        if book is not None:
            raise MarketTermsUnavailable from not_found
        raise

    if terms.status != _OPEN:
        raise MarketClosed

