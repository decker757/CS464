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

    Unlike `books.ensure_open`, this cannot roll back before the HTTP call:
    its caller may hold pending writes or locks, which a rollback would lose.
    So a caller holding a transaction pins a pooled connection for up to five
    seconds. The trade path releases its own first; #115 moves that in here.
    """
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

