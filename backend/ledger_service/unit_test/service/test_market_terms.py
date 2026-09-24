"""The terms pull: the ledger's first outbound HTTP call. [F-7] #96, D-008.

**Nothing in this repository made a service-to-service call before this one.**
Five services, and every one of them talks only to Postgres, Redis or a token
it was handed. D-008 accepts that cost knowingly, so these tests exist to pin
the parts of it that are easy to get wrong and invisible when they are: the
number format, the timeout, and what each kind of upstream failure turns into.

No database. Nothing here writes a book — `test_market_books.py` does that, and
it drives this module through the same seam. These tests are about the wire.

**How the seam works.** `market_terms.fetch` takes an injectable
`transport`, so the suite hands it an `httpx.MockTransport` and exercises the
real request building, the real URL, the real headers and the real JSON
parsing against a body the market service would actually send. That is the
whole point of doing it this way rather than monkeypatching `fetch` itself:
the bug D-016 exists to prevent lives in the parsing, and a patched-out `fetch`
would never run it.

**Three tests left this file in [F-8] #109 and live in `test_market_books.py`
now.** `test_a_market_with_null_terms_is_unavailable_rather_than_funded`,
`test_terms_that_could_never_be_priced_are_refused` and
`test_a_ten_outcome_market_is_not_refused` were rules about *writing a book* —
a `b` that can never be priced, an outcome list that can never be priced —
being enforced on a read. ADR 0017 moves them to `books.ensure_open`, where
the write is, because a market_service returning a null `b` must not start
refusing trades on books snapshotted weeks earlier. What stays here is what
any caller structurally needs: an object, a matching id, a parseable status,
and values of the right type.

Driving the real market service over `ASGITransport` is not an option and never
will be. `unit_test/test_import_boundary.py` fails any `import market_service`
from this suite, because that import succeeds under pytest — `pythonpath = . ..`
puts every service on the path — and is an `ImportError` in the container, where
the image holds only this service and `shared/`. A test that reached across
would pass in CI and the service would die on deploy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

def _terms():
    """Imported inside each test rather than at module scope.

    `service/market_terms.py` does not exist yet, and a top-level import of it
    would be one collection error that takes the whole file down as a single
    red line. Reached through here, every test below fails on its own, named
    after the criterion it is holding — which is the point of writing them
    first (D-007). Same convention as `market_service`'s `test_browsing.py`.
    """
    from service import market_terms  # noqa: PLC0415

    return market_terms


def _errors():
    """`core/errors.py` exists; three of the names below do not yet."""
    from core import errors  # noqa: PLC0415

    return errors

# A published market's terms, shaped exactly as `PublicMarketOut` serialises
# them. `liquidity_b` and `seed_subsidy` are JSON *strings*, which is D-016:
# Pydantic renders a `Decimal` that way by default and the public projection
# leaves them typed as `Decimal` on purpose, precisely so this consumer can
# read them exactly.
#
# Trimmed to what this consumer reads. The real response also carries
# `question`, `description`, `resolution_criteria`, `resolution_sources` and
# `proposed_outcome_id`; none of them is the ledger's business, and a fixture
# listing them would suggest otherwise.
_MARKET_ID = uuid.UUID("410465f3-2852-4833-964b-f42e23b8227c")
_YES = uuid.UUID("4f2a6b1e-0c3d-4a7f-9b12-8e5d6c7a4f30")
_NO = uuid.UUID("9d1c5e84-7b2a-4f13-a6c8-2e0b9d4f7a15")


def _terms_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": str(_MARKET_ID),
        "status": "open",
        "close_time": "2027-01-05T12:00:00Z",
        "resolution_time": "2027-01-20T12:00:00Z",
        "liquidity_b": "100.0000",
        "seed_subsidy": "250.0000",
        "published_at": "2026-09-01T09:00:00Z",
        "outcomes": [
            {"id": str(_YES), "position": 0, "label": "Yes"},
            {"id": str(_NO), "position": 1, "label": "No"},
        ],
    }
    body.update(overrides)
    return body


def _responds(
    status_code: int = 200,
    body: dict[str, object] | None = None,
    *,
    record: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A transport that answers every request the same way.

    `record` collects the requests that were made, for the tests that assert on
    the URL and the forwarded token rather than on the answer.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return httpx.Response(
            status_code,
            json=_terms_body() if body is None else body,
        )

    return httpx.MockTransport(handler)


def _raises(exc: Exception) -> httpx.MockTransport:
    """A transport whose every request fails the way httpx would."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def _token() -> str:
    from unit_test.conftest import mint_token  # noqa: PLC0415

    return mint_token(uuid.uuid4())


# --- D-016: the number format, which is the whole reason this is a string --
async def test_liquidity_b_arrives_as_the_exact_decimal_that_was_sent() -> None:
    """D-016, and the reason `PublicMarketOut` sends a string at all.

    The value here is chosen to fail if the number goes anywhere near a float,
    and that choice is the entire test. `Decimal("100.0000")` survives
    `Decimal(str(float(...)))` perfectly, so asserting exact equality on a
    friendly value proves nothing at all — it would pass against an
    implementation that round-tripped through an IEEE double on every call.

    `12345678901234.5678` is eighteen significant digits. It fits
    `Numeric(18, 4)` exactly, which is the column both services store this in,
    and float64 carries about 15.95 decimal digits — so it is the smallest
    realistic value that a float path cannot reproduce. Under `b`, an error
    here is not a rounding artefact on a display: it is at the root of every
    price the platform ever quotes.
    """
    exact = Decimal("12345678901234.5678")

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(liquidity_b=str(exact))),
    )

    assert terms.liquidity_b == exact
    assert isinstance(terms.liquidity_b, Decimal)


async def test_seed_subsidy_arrives_as_the_exact_decimal_too() -> None:
    """The same rule for the money half.

    `seed_subsidy` is posted as a real ledger transaction, so a value that has
    been through a float is a pool funded with the wrong number of credits and
    a `SUM(amount)` over the whole table that no longer comes to zero.
    """
    exact = Decimal("99999999999999.9999")

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(seed_subsidy=str(exact))),
    )

    assert terms.seed_subsidy == exact
    assert isinstance(terms.seed_subsidy, Decimal)


async def test_a_json_number_in_the_response_is_still_read_exactly() -> None:
    """Defence in depth, against the upstream contract changing under us.

    D-016 is a decision recorded in this repository, not a law of physics, and
    `MarketOut` right next door sends these as JSON numbers deliberately. If
    somebody ever points this client at the wrong projection, or `json.loads`
    is handed a body built by something that did not read D-016, the value
    arrives as a Python `float` and the exactness is already gone before this
    module sees it.

    Parsing with `parse_float=Decimal` costs nothing and makes that
    unreachable. This asserts the client does that rather than trusting the
    sender.
    """
    exact = Decimal("12345678901234.5678")
    raw = json.dumps(_terms_body()).replace('"100.0000"', str(exact))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=httpx.MockTransport(handler)
    )

    assert terms.liquidity_b == exact


async def test_the_outcomes_arrive_with_their_ids_and_positions() -> None:
    """`ledger.market_outcomes` is keyed on these two and stores no label.

    The label is display prose the market service owns — copying it here would
    be a second source of truth for a string that can be edited on a draft and
    is read by nobody on this side.
    """
    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds()
    )

    assert [(o.outcome_id, o.position) for o in terms.outcomes] == [
        (_YES, 0),
        (_NO, 1),
    ]
    assert not any(hasattr(o, "label") for o in terms.outcomes)


async def test_published_at_is_carried_through() -> None:
    """The ledger checks it rather than inferring publication from the 200.

    `browsing.get_published` already refuses a draft or a submitted market with
    a 404, so a 200 does imply published today. This reads the column anyway,
    because "published" is the condition the book actually depends on and a
    consumer that inferred it from a status code would be silently wrong the
    day that route's visibility rule changed.
    """
    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds()
    )

    assert terms.published_at == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


# --- the request this client makes ----------------------------------------
async def test_the_trader_s_token_is_forwarded_as_a_bearer_header() -> None:
    """D-018's Notes: this is a read the ledger makes on a trader's behalf.

    The header rather than a cookie, and it matters which.
    `market_service.controller.transport.extract_access_token` prefers
    `Authorization` over the cookie precisely so that "a service call carrying
    its own token is never silently reinterpreted as whoever happens to be
    logged in" — so a forwarded bearer lands as the trader it belongs to.

    Safe here because the public market read mints nothing and changes nothing.
    It is not a precedent for a ledger *write*, which is the question ADR 0009
    deferred to [T-2] #22 and which is still open.
    """
    token = _token()
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=token, transport=_responds(record=seen)
    )

    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == f"Bearer {token}"


async def test_it_asks_the_public_detail_endpoint_for_that_market() -> None:
    """The contract is `GET /public/markets/{id}`, not the admin route.

    `/markets/{id}` exists, is scoped to the creator and requires an admin — a
    client pointed at it would 403 every trader in the system, and the one
    admin it worked for would be reading a different projection with
    `liquidity_b` as a float.
    """
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds(record=seen)
    )

    assert seen[0].url.path == f"/public/markets/{_MARKET_ID}"
    assert seen[0].method == "GET"


async def test_the_request_carries_an_explicit_timeout() -> None:
    """D-030. Every phase bounded, at the values this service chose.

    This call happens inside a request that holds a database session and, on
    the trade path, row locks. A market service that accepts the connection and
    then stops answering would hold all of that open for as long as the socket
    survives — so the failure mode of a hung dependency is a ledger that cannot
    write, rather than a trade that fails and frees its locks.

    Asserted on the request's own extensions rather than on the client, because
    that is where the value that will actually be enforced ends up.

    **This test cannot fail if `timeout=_TIMEOUT` is deleted, and that is a
    fact about the values rather than a weakness here.** `_TIMEOUT` is five
    seconds on all four phases, which is exactly `DEFAULT_TIMEOUT_CONFIG`, so
    there is no observable difference between stating it and inheriting it.
    The earlier version of this test asserted only that `connect` and `read`
    were non-null, which was true of the default too. Pinning the values at
    least makes a *change* to the budget visible — and if the budget is ever
    chosen deliberately rather than matched to httpx's, this test starts being
    able to catch the deletion as well.
    """
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds(record=seen)
    )

    timeout = seen[0].extensions.get("timeout")
    assert timeout is not None, "no timeout was attached to the request"
    assert timeout == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}, (
        f"the request carried a budget nobody recorded a reason for: {timeout}"
    )


# --- D-030: what each kind of failure becomes -----------------------------
async def test_a_connection_error_is_unavailable_not_a_crash() -> None:
    """The market service is down, or the name does not resolve.

    503 because the request was fine and the dependency was not, and because a
    trade that failed this way is worth retrying in a moment. Letting
    `httpx.ConnectError` escape would surface as a 500, which says the ledger
    has a bug.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_raises(httpx.ConnectError("nope")),
        )


async def test_a_timeout_is_unavailable() -> None:
    """The market service accepted the connection and then stopped talking.

    Same answer as a refused connection, deliberately. From the caller's side
    they are the same event — the terms did not arrive and trying again later
    is the right move.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_raises(httpx.ReadTimeout("slow")),
        )
