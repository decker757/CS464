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
    not JSON, JSON that is not an object, a field of the wrong type, a null
    `liquidity_b` or `seed_subsidy`, and a body whose `id` is some other
    market's. `_parse` below holds all of it, and says why it is one `try`
    rather than several.

    A published market's `liquidity_b` and `seed_subsidy` are refused as
    unavailable too if either is null on the wire — structurally possible
    (`MarketDraftRequest` lets a draft omit both) and unreachable in practice,
    since `publish` re-runs every submission rule — and so is a `liquidity_b`
    of zero or below, which the engine cannot price. Refusing beats writing a
    book with a `b` that can never be priced.
    """
    settings = get_settings()

    # A client per call, rather than one held for the process. It builds a
    # connection pool that serves exactly one request and is then closed, so
    # nothing here is reused: DNS, TCP and TLS are paid every time.
    #
    # Affordable because of where this sits. `books.ensure_open` calls it only
    # when a market has no book — once per market, ever — and every later
    # touch returns on the fast path without reaching this module at all. The
    # cost is one handshake per market, against a first touch that is already
    # doing a round trip to another service and three inserts.
    #
    # A module-level client would be faster and would buy two problems: it
    # needs closing in `main.py`'s lifespan, and it fixes the transport at
    # construction, which is the seam the whole test suite drives this through.
    # Revisit if a second caller appears that is not once-per-market.
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
    """
    try:
        if not isinstance(body, dict):
            # A JSON array or scalar. `.get` would be an `AttributeError`.
            raise TypeError("body is not an object")

        liquidity_b = _to_decimal(body.get("liquidity_b"))
        seed_subsidy = _to_decimal(body.get("seed_subsidy"))
        if liquidity_b is None or seed_subsidy is None:
            raise MarketTermsUnavailable
        # A `b` of zero divides by zero in the engine and a negative one
        # inverts the cost function: priced never, on an immutable book.
        #
        # `is_finite()` first. `Decimal` takes `"Infinity"` and `"NaN"` from
        # JSON as readily as `"100"`, and the two get past a bare `<= 0` in
        # opposite ways.
        #
        # `Decimal("Infinity") <= 0` is simply `False`, so it passed. It
        # cannot actually be stored — `numeric(18, 4)` answers "a field with
        # precision 18, scale 4 cannot hold an infinite value" — so the old
        # behaviour was the INSERT failing with a `DataError`, which is not
        # an `IntegrityError` and so escapes the lost-race handler in
        # `books.ensure_open` as an unmapped 500 on a market's first touch.
        # Bounded, but the wrong code for an upstream fault.
        #
        # `Decimal("NaN") <= 0` *raises* `InvalidOperation`, which this
        # function's `except ArithmeticError` turns into the 503 by accident.
        # That one matters more than infinity does, because `numeric(18, 4)`
        # stores NaN perfectly happily — so a NaN reaching the write is a
        # book ADR 0005 makes immutable, and every price of it thereafter.
        # Both named here so neither depends on an accident.
        if not liquidity_b.is_finite() or liquidity_b <= 0:
            raise MarketTermsUnavailable
        # The subsidy has the same shape and had only a null check.
        # market_service requires it greater than zero
        # (`validation.py::_liquidity_problems`, "The seed subsidy must be
        # greater than zero"), and the two bad values fail differently and
        # both badly. A *negative* one funds the pool backwards:
        # `ensure_open` posts `Leg(platform, -(-250)) = +250` against
        # `Leg(pool, -250)`, so the pool is debited and the house credited,
        # and `_refuse_overdrafts` exempts only PLATFORM — it surfaces much
        # later as an `InsufficientFunds` 409 on somebody's ordinary first
        # preview, about a balance that is not theirs. A *zero* one builds
        # two zero legs and `posting.post` calls that `UnbalancedTransaction`
        # — a 422 blaming the caller for terms they never sent.
        if not seed_subsidy.is_finite() or seed_subsidy <= 0:
            raise MarketTermsUnavailable

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
        _refuse_unpriceable(outcomes)

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


# The fewest outcomes a market can be priced with. Public because
# `service/preview.py` holds the same floor on the way out: a book that
# already exists with fewer rows is as unpriceable as terms that arrive
# with fewer, and only one of those is something this module sees. Two, the same floor
# `market_service/service/validation.py::MIN_OUTCOMES` enforces at submission
# — restated rather than imported, because `unit_test/test_import_boundary.py`
# fails any `import market_service` from this service and is right to: that
# import resolves under pytest and is an ImportError in the container.
MIN_OUTCOMES = 2


def _refuse_unpriceable(outcomes: list[OutcomeTerms]) -> None:
    """Terms that could be stored but never priced. Same defence as null `b`.

    Three ways a well-formed outcome list is still unusable, and none of them
    is reachable from a correct market service — `publish` re-runs every
    submission rule, which requires between two and ten named outcomes with
    server-assigned positions. They are refused for the reason the null-terms
    check is: the book is written once and is immutable under ADR 0005, so a
    bad one is not something a later read corrects.

    **Fewer than two outcomes.** `C(q) = b·ln(Σ e^(q_i/b))` over one outcome
    prices it at 1.0 and over none is a sum with no terms. Either way the
    market opens, funds its pool, and quotes a price nobody can trade against.

    **A repeated outcome id or position.** Both are unique constraints on
    `market_outcomes`, so these reach the database and fail there — inside
    `books.ensure_open`'s savepoint, where the `except IntegrityError` is
    watching for a *lost first-touch race*. It re-raises correctly, because
    `_find` finds no committed book, but the request ends as a 500 on a
    condition that is the upstream being wrong. Caught here it is the 503 the
    contract promises, and the race handler keeps meaning only what it says.
    """
    if len(outcomes) < MIN_OUTCOMES:
        raise MarketTermsUnavailable
    if len({o.outcome_id for o in outcomes}) != len(outcomes):
        raise MarketTermsUnavailable
    if len({o.position for o in outcomes}) != len(outcomes):
        raise MarketTermsUnavailable


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
