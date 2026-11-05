"""A market's terms from market_service, for settlement and void. [F-8] #109, ADR 0017.

The book holds no status, so settlement and void ask market_service's public
detail endpoint, holding no connection while it answers. The trade path no
longer comes here: it reads the ledger's local copy (`service/local_copy.py`,
ADR 0030).
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import release_before_calling_out
from core.errors import MarketNotFound, MarketTermsUnavailable
from service import books, market_terms
from service.market_terms import MarketTerms


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
    before it would be released mid-request, which ADR 0015 forbids.
    `release_before_calling_out` asserts against a pending write and cannot
    see a `SELECT ... FOR UPDATE`. The rollback also expires every ORM
    instance the session has loaded.
    """
    # D-043: release the connection a read before this autobegan, so the call
    # (up to 5s) holds none. The `books.find` below autobegins its own.
    await release_before_calling_out(session, caller="read_terms()")
    try:
        return await market_terms.fetch(
            market_id, access_token=access_token, terms_client=terms_client
        )
    except MarketNotFound as not_found:
        book = await books.find(session, market_id)
        if book is not None:
            raise MarketTermsUnavailable from not_found
        raise

