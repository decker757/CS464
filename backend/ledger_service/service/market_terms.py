"""The terms pull: the ledger's first outbound HTTP call. [F-7] #96, D-008, D-031.

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

# D-030. This call sits inside a request that may already hold a database
# session and, on the trade path, row locks — a market service that accepts
# the connection and then stops answering must not be allowed to hold any of
# that open for as long as the socket survives.
#
# These are httpx's own defaults, stated rather than inherited. The record
# used to claim httpx left `read` unbounded; it does not —
# `DEFAULT_TIMEOUT_CONFIG` is `Timeout(timeout=5.0)`, all four phases. So this
# line changes no behaviour today and no test can catch its deletion. It is
# here so the number has somewhere to live, and D-030 carries the open
# question of whether five seconds is the right budget for a call made while
# holding row locks.
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
    """A market's terms, read once and handed to `service/books.py` to copy.

    `status` is the wire string from market_service's public projection —
    already ADR 0011's derived predicate applied, so it is the one field that
    answers both the clock's close and an administrator's early one. Carried
    here and never gated on: `service/market_status.py::ensure_trading` is
    the only place in this service allowed to turn it into a refusal
    (ADR 0017).

    `liquidity_b` and `seed_subsidy` are `Decimal | None` because the wire
    contract they are read from (`PublicMarketOut`) declares them nullable.
    Structurally possible on a draft and unreachable on anything `fetch`
    returns a 200 for; `service/books.py::ensure_open` is what refuses a null
    one, because the refusal is about *writing an immutable book*, not about
    reading terms for a market that may be years past its first touch.

    `status` has no default, and must never get one. `_parse` refuses a
    missing status as a 503, and a default would undo that for every other
    construction path — a test double standing in for `fetch`, a cache, a
    second parse — by handing out `"open"` for a status nobody supplied. On
    the money gate, that is a fail-open no test would see.
    `test_market_terms_without_a_status_cannot_be_built` holds it.
    """

    market_id: uuid.UUID
    status: str
    liquidity_b: Decimal | None
    seed_subsidy: Decimal | None
    published_at: datetime | None
    outcomes: list[OutcomeTerms]


async def fetch(
    market_id: uuid.UUID,
    *,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MarketTerms:
    """The market's terms, or the mapped error D-030 assigns to what went wrong.

    | upstream | raised | status |
    | --- | --- | --- |
    | connect error, timeout, 5xx | `MarketTermsUnavailable` | 503 |
    | a 200 that is not this market | `MarketTermsUnavailable` | 503 |
    | 404 | `MarketNotFound` | 404 |
    | 401 | `NotAuthenticated` | 401 |

    "Not this market" is every way a 200 can fail to be usable: bytes that are
    not JSON, JSON that is not an object, a field of the wrong type, a
    `status` that is missing or not a string, and a body whose `id` is some
    other market's. `_parse` below holds all of it, and says why it is one
    `try` rather than several.

    **This function carries what it reads and decides nothing on it.**
    `liquidity_b`, `seed_subsidy` and the outcome list are handed back exactly
    as parsed, null or unpriceable or not — ADR 0017: those are rules about
    *writing a book*, and `service/books.py::ensure_open` is where they are
    enforced now. `status` is carried the same way; only
    `service/market_status.py::ensure_trading` may turn it into a refusal.
    `test_the_close_time_is_not_what_decides_anything_here` pins the general
    shape this is one instance of: a market past `close_time`, or not open,
    still has terms, and this function still hands them back.
    """
    settings = get_settings()

    # A client per call, rather than one held for the process. It builds a
    # connection pool that serves exactly one request and is then closed, so
    # nothing here is reused: DNS, TCP and TLS are paid every time.
    #
    # **That is no longer cheap, and this is the known cost rather than a
    # justification.** It was written when `books.ensure_open` was the only
    # caller — once per market, ever. `market_status.ensure_trading` (ADR
    # 0017) now calls it on every trade that is not a replay, so every trade
    # pays a fresh handshake to market_service, on the hottest path there is.
    #
    # The fix is one `httpx.AsyncClient` for the process, opened and closed on
    # `main.py`'s lifespan the way the Redis client already is, with the
    # transport still injectable per call for the suite. That is the named
    # follow-up and its own ticket (#114), not a change to make in passing here: it
    # moves the seam every test in this module and in `test_market_status.py`
    # drives through, and it wants the lifespan pattern done once, properly.
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

    return _parse(market_id, body)


def _parse(market_id: uuid.UUID, body: object) -> MarketTerms:
    """A decoded body to `MarketTerms`, or `MarketTermsUnavailable`.

    **Every step in here is inside one `try`, and that is the whole reason it
    is a function.** The table above promises that a malformed body is a 503,
    and valid JSON is not a valid market: `json.loads` succeeding says only
    that the bytes parsed. Before this, a body that decoded and then failed to
    make sense raised whatever the first bad field happened to raise —
    `InvalidOperation` on a `liquidity_b` of `"abc"`, `KeyError` on a missing
    `id`, `ValueError` on an outcome id that is not a UUID, `AttributeError`
    on a JSON array — none of them a `LedgerError`, so none of them mapped,
    and all of them a 500 on the trade path where the contract says 503.

    Nothing writes before `fetch` returns, so the old behaviour corrupted
    nothing. It reported the wrong thing about a dependency that was, in every
    one of those cases, not the market service.

    Listing the exception types rather than catching `Exception`: these are
    the failures of *parsing a value*, and a `MemoryError` or a
    `KeyboardInterrupt` arriving mid-parse is not the market service being
    malformed.

    **What stays here, since ADR 0017.** What any caller structurally needs:
    an object, a matching id, a parseable `status`, and every field typed
    correctly. A `liquidity_b`/`seed_subsidy` of the wrong *type* (a bool, an
    unparseable string) is still refused here — that is a malformed response,
    not a term this side has an opinion about. **What moved to
    `service/books.py::ensure_open`:** refusing a *null* `liquidity_b` or
    `seed_subsidy`, and refusing an outcome list that could be stored but
    never priced (`_refuse_unpriceable`). Those are rules about writing an
    immutable book, and enforcing them here would mean a market_service that
    started returning a null `b` refusing trades on books snapshotted weeks
    earlier, against a value this service read once and never rereads.
    """
    try:
        if not isinstance(body, dict):
            # A JSON array or scalar. `.get` would be an `AttributeError`.
            raise TypeError("body is not an object")

        liquidity_b = _to_decimal(body.get("liquidity_b"))
        seed_subsidy = _to_decimal(body.get("seed_subsidy"))

        # ADR 0017's third structural rule, beside "an object" and "a
        # matching id": a parseable status. Every non-string case is
        # `!= "open"`, so a naive comparison downstream would refuse it as a
        # closed market with no exception anywhere — the loudest kind of
        # quiet, in a system where nothing reopens a market. Refused here
        # instead, as the 503 that means the dependency is the problem.
        status = body.get("status")
        if not isinstance(status, str):
            raise TypeError("status is not a string")

        published_raw = body.get("published_at")
        published_at = (
            datetime.fromisoformat(published_raw) if published_raw else None
        )

        outcomes = [
            OutcomeTerms(
                outcome_id=uuid.UUID(str(o["id"])), position=int(o["position"])
            )
            for o in body.get("outcomes", [])
        ]

        # The response's own id, checked against the one asked for. It was
        # parsed and then never read before, which made it a field that could
        # only fail — and it is worth reading: a proxy or a cache answering
        # `/public/markets/{a}` with market `b`'s body would otherwise open
        # a's book carrying b's `liquidity_b`, permanently, because the
        # snapshot is immutable by design. Unreachable through a correct
        # market service, like everything else in this function.
        returned_id = uuid.UUID(str(body["id"]))
        if returned_id != market_id:
            raise MarketTermsUnavailable

        return MarketTerms(
            market_id=returned_id,
            status=status,
            liquidity_b=liquidity_b,
            seed_subsidy=seed_subsidy,
            published_at=published_at,
            outcomes=outcomes,
        )
    except (
        ArithmeticError,  # InvalidOperation, from Decimal
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise MarketTermsUnavailable from exc


def _to_decimal(value: object) -> Decimal | None:
    """A JSON string or number to `Decimal`, exactly. Never via `float`.

    **`bool` is refused explicitly, and it is the only case here that failed
    silently rather than loudly.** `bool` is a subclass of `int`, so
    `Decimal(True)` is `Decimal(1)` — no exception, no warning. A
    `liquidity_b` of JSON `true` opened a book at `b = 1` instead of whatever
    the market was configured with, and since ADR 0005 makes that snapshot
    immutable, every price that market ever quoted would have been wrong with
    nothing anywhere to notice. Every other malformed value raised something.

    `float` is absent from the accepted types on purpose rather than by
    omission: `parse_float=Decimal` above means a JSON number reaches here as
    a `Decimal` already, so a `float` arriving would mean that setting had
    been dropped — which is precisely the loss D-033 says cannot be detected
    after the fact. Refusing is the only honest answer to it.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"{type(value).__name__} is not a decimal value")
    return Decimal(value)
