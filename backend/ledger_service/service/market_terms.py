"""The terms pull: the ledger's first outbound HTTP call. [F-7] #96, D-008, D-029.

`fetch` reads `GET /public/markets/{id}` on market_service, forwarding the
caller's own bearer token (D-018's Notes: this is a read the ledger makes on a
trader's behalf, and it is safe because a public read mints nothing and
reveals nothing beyond what that route already shows).

`transport` is injectable so the suite can drive the real request-building,
the real headers and the real decimal-string parsing against an
`httpx.MockTransport`, without a market service running and without crossing
the service boundary `unit_test/test_import_boundary.py` enforces.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

from core.config import get_settings
from core.errors import MarketNotFound, MarketTermsUnavailable, NotAuthenticated

# D-028. httpx's own default is five seconds to connect and no ceiling on
# read. This call sits inside a request that may already hold a database
# session and, on the trade path, row locks — a market service that accepts
# the connection and then stops answering must not be allowed to hold any of
# that open for as long as the socket survives.
_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)


@dataclass(frozen=True)
class OutcomeTerms:
    """One outcome, as the ledger needs it. No `label` — that is display prose
    market_service owns, and copying it here would be a second source of
    truth for a string this side never renders."""

    outcome_id: uuid.UUID
    position: int


@dataclass(frozen=True)
class MarketTerms:
    """A market's terms, read once and handed to `service/books.py` to copy."""

    market_id: uuid.UUID
    liquidity_b: Decimal
    seed_subsidy: Decimal
    published_at: datetime | None
    outcomes: list[OutcomeTerms]


async def fetch(
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.BaseTransport | None = None,
) -> MarketTerms:
    """The market's terms, or the mapped error D-028 assigns to what went wrong.

    | upstream | raised | status |
    | --- | --- | --- |
    | connect error, timeout, 5xx, malformed body | `MarketTermsUnavailable` | 503 |
    | 404 | `MarketNotFound` | 404 |
    | 401 | `NotAuthenticated` | 401 |

    A published market's `liquidity_b` and `seed_subsidy` are refused as
    unavailable too if either is null on the wire — structurally possible
    (`MarketDraftRequest` lets a draft omit both) and unreachable in practice,
    since `publish` re-runs every submission rule. Refusing beats writing a
    book with a `b` that can never be priced.
    """
    settings = get_settings()

    async with httpx.AsyncClient(
        base_url=settings.market_service_url,
        transport=transport,
        timeout=_TIMEOUT,
    ) as client:
        try:
            response = await client.get(
                f"/public/markets/{market_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TransportError as exc:
            raise MarketTermsUnavailable from exc

    if response.status_code == 404:
        raise MarketNotFound
    if response.status_code == 401:
        raise NotAuthenticated
    if response.status_code >= 400:
        raise MarketTermsUnavailable

    try:
        # parse_float=Decimal is what keeps a value from ever touching a
        # Python float, even in the defence-in-depth case where the field
        # arrives as a bare JSON number rather than the decimal string D-016
        # specifies: the numeral's text goes straight to `Decimal`, with no
        # float in between to have already lost precision.
        body = json.loads(response.content, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MarketTermsUnavailable from exc

    liquidity_b = _to_decimal(body.get("liquidity_b"))
    seed_subsidy = _to_decimal(body.get("seed_subsidy"))
    if liquidity_b is None or seed_subsidy is None:
        raise MarketTermsUnavailable

    published_raw = body.get("published_at")
    published_at = datetime.fromisoformat(published_raw) if published_raw else None

    outcomes = [
        OutcomeTerms(outcome_id=uuid.UUID(str(o["id"])), position=int(o["position"]))
        for o in body.get("outcomes", [])
    ]

    return MarketTerms(
        market_id=uuid.UUID(str(body["id"])),
        liquidity_b=liquidity_b,
        seed_subsidy=seed_subsidy,
        published_at=published_at,
        outcomes=outcomes,
    )


def _to_decimal(value: object) -> Decimal | None:
    """A JSON string or number to `Decimal`, exactly. Never via `float`."""
    if value is None:
        return None
    return Decimal(value)
