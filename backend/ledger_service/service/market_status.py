"""The stopped-market gate: may this market still be traded? [F-8] #109, ADR 0017.

`ledger.market_books` carries no status and no `close_time`, and neither can
be added: ADR 0011 makes the clock the authority over two values in
`market.markets`, and ADR 0014 leaves `close_time` in the future on an early
close on purpose, so a snapshot of it would accept trades against a market an
administrator deliberately stopped. The answer is a read of market_service's
public detail endpoint, whose `status` is already ADR 0011's derived
predicate — one field answering both the clock's close and an
administrator's.

**There is no trade path, and nothing here invents one.** [T-2] #22 is the
trade. `ensure_trading` is the primitive it will call: the replay-before-gate
and gate-before-lock ordering ADR 0017 requires belongs to that caller, since
it needs a trade to order against. This module only ever raises or returns
`None` — never `MarketTerms` — because handing a trade fresh terms would be
one refactor away from pricing off a wire `liquidity_b` instead of the book's
immutable snapshot (ADR 0005).
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketClosed, MarketNotFound, MarketTermsUnavailable
from service import books, market_terms

# The one `status` a trade may proceed against, spelled as market_service
# puts it on the wire. Restated rather than imported, for the reason
# `books.MIN_OUTCOMES` is: `unit_test/test_import_boundary.py` fails any
# `import market_service` from this service, and it is right to — that
# import resolves under pytest and is an `ImportError` in the container.
#
# Pinned rather than trusted. A wrong `MIN_OUTCOMES` fails loudly; a wrong
# value here is silent — every trade refused as `market_closed`, with nothing
# red on either side. So `test_market_status.py` reads `MarketStatus.OPEN`'s
# value straight out of market_service's source with `ast`, the way
# `test_price_publish.py` pins `PRICE_CHANNEL` against `realtime_service`:
# no import, no running code, and the copy cannot drift without a test
# noticing.
_OPEN = "open"


async def ensure_trading(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Raise if this market is not open for trading. Otherwise return `None`.

    One call to `market_terms.fetch`, forwarding the caller's own token and
    minting none of its own (D-018's Notes, now describing every trade).

    A 404 has two correct answers, and only `ledger.market_books` can choose
    between them — a market holding a book existed and was published, so a
    404 for it means market_service is answering incorrectly
    (`MarketTermsUnavailable`, 503); a market with no book is the ordinary
    shape of a first trade (`MarketNotFound`, 404). That read is `books.find`, unlocked —
    ADR 0015 governs reads that decide a *write*, and this one decides an
    error code — and it happens on the 404 branch only: a 200 with
    `status == "open"` sends nothing to Postgres at all.

    A 200 whose derived `status` is anything but `"open"` is `MarketClosed`
    (409). Every other upstream failure — an unreachable market_service, a
    timeout, a 5xx, an unparseable 200, a `status` that is missing or not a
    string, or a 401 — propagates from `market_terms.fetch` unchanged, so a
    sick dependency is never read as a closed market.
    """
    try:
        terms = await market_terms.fetch(
            market_id, access_token=access_token, transport=transport
        )
    except MarketNotFound as not_found:
        book = await books.find(session, market_id)
        if book is not None:
            raise MarketTermsUnavailable from not_found
        raise

    if terms.status != _OPEN:
        raise MarketClosed

