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


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_an_upstream_server_error_is_unavailable(status_code: int) -> None:
    """A 5xx is the market service saying it could not answer.

    Parametrised across the three a deployment actually produces: the service
    itself failing, and the two a proxy in front of it produces while it is
    restarting.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(status_code, body={"detail": "boom"}),
        )


async def test_an_upstream_404_is_not_unavailable() -> None:
    """D-030, and the distinction that matters most in this file.

    A 404 from `/public/markets/{id}` means one of three things and
    deliberately does not say which: no such market, a draft, or a submitted
    market. `browsing.get_published` makes them indistinguishable on purpose,
    because telling them apart would leak what administrators are half-writing.

    All three are permanent. Answering 503 would tell a trader to retry a
    market that is never going to appear, and would hide a real bug — a trade
    against an id that does not exist — inside a message about the market
    service being down.
    """
    with pytest.raises(_errors().MarketNotFound):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(404, body={"code": "market_not_found"}),
        )


async def test_an_upstream_401_propagates_as_not_authenticated() -> None:
    """The forwarded token was rejected, which is a fact about the caller.

    D-018's Notes flag exactly this: the ledger now depends on the caller's
    credentials for a request it issues for itself. A trader's access token
    lives fifteen minutes, so the honest answer is the one that tells them to
    log in again — not one that blames a dependency that is working perfectly.

    `NotAuthenticated` already exists in `core/errors.py` at 401 and is reused
    rather than duplicated, so this arrives at the client as the same shape as
    any other expired session.
    """
    with pytest.raises(_errors().NotAuthenticated):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(401, body={"code": "invalid_token"}),
        )


async def test_a_malformed_body_is_unavailable_rather_than_a_crash() -> None:
    """A 200 carrying something that is not a market.

    The shape this produces in practice is a proxy or a login page answering
    200 with HTML. Treated as unavailable, because what it means is that the
    thing on the other end is not the market service, and it must not surface
    as a parse error escaping from inside the trade path.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=httpx.MockTransport(handler)
        )


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("liquidity_b is not a numeral", _terms_body(liquidity_b="abc")),
        ("seed_subsidy is not a numeral", _terms_body(seed_subsidy="")),
        ("id is missing", {k: v for k, v in _terms_body().items() if k != "id"}),
        ("id is not a uuid", _terms_body(id="nope")),
        ("published_at is not a timestamp", _terms_body(published_at="garbage")),
        (
            "an outcome id is not a uuid",
            _terms_body(outcomes=[{"id": "nope", "position": 0}]),
        ),
        ("an outcome has no id", _terms_body(outcomes=[{"position": 0}])),
        ("a position is not a number", _terms_body(outcomes=[{"id": str(_YES), "position": "first"}])),
        ("outcomes is not a list of objects", _terms_body(outcomes=["yes", "no"])),
        ("the body is a JSON array", []),
        ("the body is a JSON string", "not a market"),
    ],
)
async def test_valid_json_that_is_not_a_market_is_unavailable(
    label: str, body: object
) -> None:
    """`json.loads` succeeding says the bytes parsed, not that this is a market.

    The test above covers bytes that are not JSON at all — a proxy's HTML.
    This covers the other half, which is every way a body can decode and then
    fail to make sense, and before D-030 was enforced properly each of these
    raised whatever the first bad field happened to raise: `InvalidOperation`,
    `KeyError`, `ValueError`, `TypeError`, `AttributeError`. None is a
    `LedgerError`, so none mapped, and each was a 500 on the trade path where
    the documented contract is 503.

    Nothing writes before `fetch` returns, so none of them corrupted anything.
    What they did was report the wrong thing about a dependency that was, in
    every one of these cases, not the market service.

    Parametrized over the *kinds* of malformation rather than a representative
    one, because each took a different route out of the function and a single
    case would have pinned a single route.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=_responds(body=body)
        )


async def test_a_true_liquidity_b_is_refused_rather_than_read_as_one() -> None:
    """The one malformation that did not raise, which is what makes it the worst.

    `bool` is a subclass of `int`, so `Decimal(True)` is `Decimal(1)` — no
    exception, no warning, no test going red. A `liquidity_b` of JSON `true`
    opened a book at `b = 1` rather than at whatever the administrator
    configured, and ADR 0005 makes that snapshot immutable: every price that
    market ever quoted would have been computed from the wrong denominator,
    permanently, with nothing anywhere to notice.

    Asserted separately from the parametrize above because the others were
    loud and this one was silent, and the fix for it is a type check rather
    than a `try`.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(body=_terms_body(liquidity_b=True)),
        )


async def test_a_body_for_a_different_market_is_refused() -> None:
    """`MarketTerms.market_id` was parsed and never read. Now it is the check.

    A cache or a proxy answering `/public/markets/{a}` with market `b`'s body
    is the shape this defends: the terms would be copied into `a`'s book under
    `a`'s id, carrying `b`'s `liquidity_b` and `b`'s outcome ids. Immutable
    once written, so there is no later read that corrects it — the book simply
    prices the wrong market forever.

    Unreachable through a correct market service, like everything else in
    `_parse`. It costs one comparison and it gives the field a reason to be
    parsed at all.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(body=_terms_body(id=str(uuid.uuid4()))),
        )


# --- [F-8] #109: the client carries the status and decides nothing --------
async def test_the_status_is_carried_through() -> None:
    """ADR 0017: `fetch` gains `status`, and it is the field the gate reads.

    Read off `PublicMarketOut`, whose `status` is already ADR 0011's predicate
    applied — `displayed_status` only ever turns OPEN into CLOSED (D-022,
    D-027) — so one field answers both the clock's close and an
    administrator's early one. That is the whole reason the gate reads this
    projection rather than restating the predicate on this side.

    Carried as the wire string. An enum here would be a second copy of
    `MarketStatus`, whose members this service cannot import and must not
    retype: `test_import_boundary.py` fails any `import market_service`,
    because that import resolves under pytest and is an `ImportError` in the
    container.
    """
    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds()
    )

    assert terms.status == "open"


@pytest.mark.parametrize(
    "status", ["closed", "pending_resolution", "approved", "settled"]
)
async def test_fetch_does_not_refuse_a_market_that_is_not_open(
    status: str,
) -> None:
    """One client, one parse path, and no opinion about what it carries.

    ADR 0017 is explicit: "The client carries the status; the trade path
    decides on it." Three readers need terms for a market nobody may trade —
    [3.4] #12's settlement reads `q` from a book whose market stopped weeks
    ago, the realtime snapshot serves a closed market's prices, and
    `books.ensure_open` gives a CLOSED market a book on purpose. A client that
    refused a non-open status would break all three, and it would do it
    silently: the first symptom is a settlement that cannot find the positions
    it is meant to pay out.

    The gate in `service/market_status.py` is the only thing in this service
    that may turn this value into a refusal.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(status=status)),
    )

    assert terms.status == status


async def test_a_null_liquidity_b_is_carried_rather_than_refused() -> None:
    """The refusal that moved, asserted from the side it moved *off*.

    `test_market_books.py::test_a_market_with_null_terms_is_unavailable_rather_than_funded`
    holds the rule at `books.ensure_open` now. This holds the other half — that
    `_parse` no longer has an opinion — and the two together are the whole of
    ADR 0017's argument: refusing a null `b` is the right answer when you are
    about to write it into an immutable book, and the wrong answer when you are
    asking whether a market is open, because the `b` that matters was
    snapshotted at first touch and is never read again.

    `MarketTerms.liquidity_b` widens to `Decimal | None` here, which is what
    `PublicMarketOut` has declared on the other side all along. Until this
    ticket the ledger's type was narrower than the endpoint's contract and the
    refusal was what papered over the gap.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(liquidity_b=None)),
    )

    assert terms.liquidity_b is None
    assert terms.seed_subsidy == Decimal("250.0000")


async def test_a_null_seed_subsidy_is_carried_rather_than_refused() -> None:
    """The money half of the same move.

    Separate from the test above because the two fields fail differently once
    they reach a book: a null `b` is a market that can never be priced, and a
    null subsidy is a pool funded with nothing. Both are `ensure_open`'s to
    refuse now, and neither is this function's.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(seed_subsidy=None)),
    )

    assert terms.seed_subsidy is None
    assert terms.liquidity_b == Decimal("100.0000")


@pytest.mark.parametrize(
    ("label", "outcomes"),
    [
        ("no outcomes at all", []),
        ("a single outcome", [{"id": str(_YES), "position": 0}]),
        (
            "the same outcome id twice",
            [{"id": str(_YES), "position": 0}, {"id": str(_YES), "position": 1}],
        ),
        (
            "two outcomes claiming one position",
            [{"id": str(_YES), "position": 0}, {"id": str(_NO), "position": 0}],
        ),
    ],
)
async def test_an_unpriceable_outcome_list_is_carried_rather_than_refused(
    label: str, outcomes: list[dict[str, object]]
) -> None:
    """`_refuse_unpriceable` moved too, and these are the four cases it held.

    Same four bodies as
    `test_market_books.py::test_terms_that_could_never_be_priced_are_refused`,
    asserted from the opposite direction: the client parses them and hands them
    on, and `books.ensure_open` is what refuses to write a book from them.

    So `MarketTerms.outcomes` may now legitimately be empty or hold one
    element. What has *not* moved is the shape of an individual outcome — an
    id that is not a UUID or a position that is not a number is still a 503
    from here, and `test_valid_json_that_is_not_a_market_is_unavailable` below
    still owns that.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(outcomes=outcomes)),
    )

    assert len(terms.outcomes) == len(outcomes)


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("status is absent", {k: v for k, v in _terms_body().items() if k != "status"}),
        ("status is null", _terms_body(status=None)),
        ("status is a number", _terms_body(status=3)),
        ("status is a list", _terms_body(status=["open"])),
        ("status is an object", _terms_body(status={"value": "open"})),
    ],
)
async def test_a_status_that_is_not_a_string_is_unavailable(
    label: str, body: object
) -> None:
    """"A parseable status" — ADR 0017's third structural rule for `_parse`.

    The field the gate decides on has to arrive as something the gate can
    compare, and the failure of it not arriving is the loudest kind of quiet:
    every case here is `!= "open"`, so a gate handed one of them refuses the
    trade as `409 market_closed` with no exception raised anywhere. A
    market_service that had broken or been misconfigured would be telling every
    trader on the platform that every market had closed — permanently, in a
    system where nothing reopens a market.

    Refused here instead, as the 503 that means the dependency is the problem.
    Same shape of defence as `_to_decimal` refusing a `bool`: the malformation
    that does not raise on its own is the one worth a rule.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=_responds(body=body)
        )


def test_market_terms_without_a_status_cannot_be_built() -> None:
    """The dataclass holds the same line `_parse` does, not a looser one.

    The test above proves `_parse` never lets an unknown status through. That
    is one construction path, and `MarketTerms` is built by others: every test
    double that stands in for `fetch`, and whatever cache or second parse path
    arrives later. A default of `"open"` on the field opened the gate for all
    of them — a market whose status nobody supplied read as tradeable, with no
    exception anywhere, on the one check that stands between a trader and a
    closed market. No test could see it while `_parse` was the only caller,
    because `_parse` always passes one.

    So a value built without a status is not a value at all.
    """
    with pytest.raises(TypeError):
        _terms().MarketTerms(  # type: ignore[call-arg]
            market_id=_MARKET_ID,
            liquidity_b=None,
            seed_subsidy=None,
            published_at=None,
            outcomes=[],
        )


async def test_a_ten_outcome_market_still_parses() -> None:
    """The ceiling is the market service's, and the floor left this file.

    `test_a_ten_outcome_market_is_not_refused` moved to `test_market_books.py`
    with the rule it argues about. What is left here is the narrower claim that
    a ten-outcome body parses into ten `OutcomeTerms` — no count rule of any
    kind survives in `_parse`, in either direction.
    """
    outcomes = [{"id": str(uuid.uuid4()), "position": i} for i in range(10)]

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(outcomes=outcomes)),
    )

    assert len(terms.outcomes) == 10


async def test_the_close_time_is_not_what_decides_anything_here() -> None:
    """A market past its close time still has terms, and still gets them.

    ADR 0011 stops *trading* at `close_time`; it does not unpublish a market.
    [3.4] #12's settlement reads `q` from a book belonging to a market that
    stopped trading weeks earlier, and the realtime snapshot endpoint serves a
    closed market's prices. A client that refused terms once `close_time` had
    passed would make both impossible.

    `PublicMarketOut` reports such a market as `closed` (D-022), which is why
    the status is not the gate either. `published_at` is.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(
            body=_terms_body(
                status="closed",
                close_time=(datetime.now(UTC) - timedelta(days=7)).isoformat(),
            )
        ),
    )

    assert terms.liquidity_b == Decimal("100.0000")
    assert terms.published_at is not None
