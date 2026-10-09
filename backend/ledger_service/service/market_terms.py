"""The terms pull: `GET /public/markets/{id}` on market_service. [F-7] #96, D-008, D-031.

Forwards the caller's own bearer token (D-018). The client is passed in, one
for the process (#114), and `open_client` takes a transport, so the suite
drives the real request and parsing through `httpx.MockTransport`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

from core.config import get_settings
from core.errors import (
    MarketNotFound,
    MarketTermsUnavailable,
    NotAuthenticated,
    SettlementUnconfirmed,
)
from core.not_found_cache import NotFoundCache

_log = logging.getLogger(__name__)

# D-030: stated rather than inherited, though it equals httpx's default, so a
# hung market_service cannot hold the caller's request open. No test can catch
# its deletion; whether five seconds is the right budget is open there.
_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)

# How long a 404 is remembered, which is also the longest a newly published
# market can still be told it does not exist, and how many ids are held at
# once, so the memory stays bounded. Per process, and only a 404 goes in: a
# 503, a timeout or a 401 says nothing about the market. DECISIONS.md, "A
# market_service 404 is remembered for ten seconds, per process".
_NOT_FOUND_TTL_SECONDS = 10.0
_NOT_FOUND_MAX_ENTRIES = 10_000

_recent_not_found = NotFoundCache(
    ttl_seconds=_NOT_FOUND_TTL_SECONDS, max_entries=_NOT_FOUND_MAX_ENTRIES
)


@dataclass(frozen=True)
class OutcomeTerms:
    """One outcome, as the ledger needs it. No `label`: that is display prose
    market_service owns."""

    outcome_id: uuid.UUID
    position: int


@dataclass(frozen=True)
class MarketTerms:
    """A market's terms, read once and handed to `service/books.py` to copy.

    `status` is market_service's derived status (ADR 0011), carried and never
    gated on: only `market_status.ensure_trading` refuses on it (ADR 0017).
    The money fields are nullable because the wire contract is;
    `books.ensure_open` refuses a null one.

    `status` must never get a default: it would hand out "open" for a status
    nobody supplied, a silent fail-open on the money gate. Neither does
    `settleable`: `False` hides a broken deploy as a window that never
    closes, and `True` pays out inside it (ADR 0019).
    """

    market_id: uuid.UUID
    status: str
    liquidity_b: Decimal | None
    seed_subsidy: Decimal | None
    published_at: datetime | None
    outcomes: list[OutcomeTerms]
    proposed_outcome_id: uuid.UUID | None
    settleable: bool


def _to_decimal(value: object) -> Decimal | None:
    """A JSON string or number to `Decimal`, exactly. Never via `float`.

    `bool` is refused explicitly: it subclasses `int`, so JSON `true` would
    silently become `b = 1` in an immutable book. A `float` here means
    `parse_float=Decimal` was dropped, a loss D-033 says cannot be detected
    afterwards, so it is refused too.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"{type(value).__name__} is not a decimal value")
    return Decimal(value)


def _to_position(value: object) -> int:
    """An outcome's position, which the contract types `int`.

    `int()` would truncate 1.5 (arriving as `Decimal` via `parse_float`) and
    read `true` as 1, so anything but a JSON integer is refused.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{type(value).__name__} is not a position")
    return value


def _to_published_at(value: object) -> datetime | None:
    """`published_at`, where null and only null means unpublished.

    Any other falsy value is malformed, not unpublished: `""` and `false`
    would otherwise read as a market that was never published, a 409.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{type(value).__name__} is not a timestamp")
    return datetime.fromisoformat(value)


def _to_uuid(value: object) -> uuid.UUID:
    """An id, which the contract sends as a string. `str()` of a JSON number
    with 32 digits is 32 hex digits, and `uuid.UUID` would take it.
    """
    if not isinstance(value, str):
        raise TypeError(f"{type(value).__name__} is not a uuid string")
    return uuid.UUID(value)


def _to_settleable(value: object) -> bool:
    """`settleable`, which must be a JSON boolean.

    Never coerced: `"false"` is truthy, and reading it as true would pay out
    inside the dispute window. `0` is refused too, since `bool` is the only
    type the contract sends.
    """
    if not isinstance(value, bool):
        raise TypeError(f"{type(value).__name__} is not a boolean")
    return value


def _to_proposed_outcome_id(value: object) -> uuid.UUID | None:
    """`proposed_outcome_id`, where null means no outcome is proposed."""
    if value is None:
        return None
    return _to_uuid(value)


def _parse(market_id: uuid.UUID, body: object) -> MarketTerms:
    """A decoded body to `MarketTerms`, or `MarketTermsUnavailable`.

    One `try`, so every malformed body is the 503 the table promises: each bad
    field would otherwise raise its own non-`LedgerError` and surface as a
    500. Parsing failures are listed rather than `Exception` caught. Only
    structural rules live here (ADR 0017); rules about writing a book are in
    `books.ensure_open`.
    """
    try:
        if not isinstance(body, dict):
            # A JSON array or scalar. `.get` would be an `AttributeError`.
            raise TypeError("body is not an object")

        liquidity_b = _to_decimal(body.get("liquidity_b"))
        seed_subsidy = _to_decimal(body.get("seed_subsidy"))

        # ADR 0017: a parseable status. A non-string would compare
        # `!= "open"` and be refused as a closed market with nothing logged;
        # the dependency is the problem, so a 503.
        status = body.get("status")
        if not isinstance(status, str):
            raise TypeError("status is not a string")

        # Indexed, not `.get`: the contract always sends the key, so a missing
        # one is a malformed body, not an unpublished market.
        published_at = _to_published_at(body["published_at"])

        # `.get` is safe here where it was not for `published_at`: a missing
        # list reads as empty, which `books.ensure_open` refuses as the same
        # 503, and the status gate never needed the outcomes.
        outcomes = [
            OutcomeTerms(
                outcome_id=_to_uuid(o["id"]), position=_to_position(o["position"])
            )
            for o in body.get("outcomes", [])
        ]

        # Indexed like `published_at`: the contract always sends both keys,
        # so a missing one is a broken deploy, not a default. DECISIONS.md,
        # "`MarketTerms` carries `proposed_outcome_id` and `settleable`".
        proposed_outcome_id = _to_proposed_outcome_id(body["proposed_outcome_id"])
        settleable = _to_settleable(body["settleable"])

        # The body's own id must match: a proxy answering with another
        # market's body would otherwise open this book with that market's `b`,
        # permanently.
        returned_id = _to_uuid(body["id"])
        if returned_id != market_id:
            raise MarketTermsUnavailable

        return MarketTerms(
            market_id=returned_id,
            status=status,
            liquidity_b=liquidity_b,
            seed_subsidy=seed_subsidy,
            published_at=published_at,
            outcomes=outcomes,
            proposed_outcome_id=proposed_outcome_id,
            settleable=settleable,
        )
    except (
        ArithmeticError,  # InvalidOperation, from Decimal
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise MarketTermsUnavailable from exc


def open_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The client every terms pull goes through: market_service's base URL
    and D-030's timeout. The caller closes it.

    `main.py`'s lifespan opens one for the process (#114). `transport` is the
    suite's seam: it wraps an `httpx.MockTransport` in the same client.
    """
    return httpx.AsyncClient(
        base_url=get_settings().market_service_url,
        transport=transport,
        timeout=_TIMEOUT,
    )


async def fetch(
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> MarketTerms:
    """The market's terms, or the error D-030 and ADR 0017 map a failure to.

    | upstream | raised | status |
    | --- | --- | --- |
    | connect error, timeout, 5xx | `MarketTermsUnavailable` | 503 |
    | a 200 that does not decode, or is not this market | `MarketTermsUnavailable` | 503 |
    | 404 | `MarketNotFound` | 404 |
    | 401 | `NotAuthenticated` | 401 |

    A 404 is remembered for ten seconds, and a repeat inside that window
    raises `MarketNotFound` with no call (DECISIONS.md, "A market_service 404
    is remembered for ten seconds, per process").

    `terms_client` comes from `open_client` and is left open: it is shared
    by every request in the process. Carries what it reads and decides
    nothing on it (ADR 0017): null or unpriceable terms and a status that is
    not open are all handed back.
    """
    if _recent_not_found.is_remembered(market_id):
        raise MarketNotFound

    try:
        response = await terms_client.get(
            f"/public/markets/{market_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    # `RequestError`, not `TransportError`, which misses
    # `httpx.DecodingError` (a lying `Content-Encoding`). Everything under
    # `RequestError` means the dependency failed: a 503.
    except httpx.RequestError as exc:
        raise MarketTermsUnavailable from exc

    if response.status_code == 404:
        _recent_not_found.remember(market_id)
        raise MarketNotFound
    if response.status_code == 401:
        raise NotAuthenticated
    if response.status_code >= 400:
        raise MarketTermsUnavailable

    try:
        # D-033: a bare JSON number goes from its text straight to `Decimal`,
        # never through a float.
        body = json.loads(response.content, parse_float=Decimal)
    # `ValueError` covers `JSONDecodeError`, `UnicodeDecodeError` and an integer
    # past Python's digit limit; `RecursionError` is nesting past the decoder's.
    except (ValueError, RecursionError) as exc:
        raise MarketTermsUnavailable from exc

    return _parse(market_id, body)


async def mark_settled(
    market_id: uuid.UUID,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> None:
    """Tell market_service the payouts committed: `POST /markets/{id}/settle`.

    Returns `None` on a `200` and raises `SettlementUnconfirmed` on anything
    else, including `httpx.RequestError`. The response body is never read, and
    the 404 cache is neither consulted nor fed: a 404 here comes from a market
    whose book exists. Logs the failure at ERROR, the only 503 in the service
    that is, because it leaves money moved and the market unmarked until a
    person repeats the request. DECISIONS.md, "`market_terms.mark_settled`
    accepts only a `200`".
    """
    try:
        response = await terms_client.post(
            f"/markets/{market_id}/settle",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.RequestError as exc:
        _log.error(
            "settlement of market %s was not confirmed: %s",
            market_id,
            type(exc).__name__,
        )
        raise SettlementUnconfirmed from exc

    if response.status_code != 200:
        _log.error(
            "settlement of market %s was not confirmed: market_service "
            "answered %s",
            market_id,
            response.status_code,
        )
        raise SettlementUnconfirmed
