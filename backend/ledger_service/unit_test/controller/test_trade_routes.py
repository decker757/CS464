"""Status codes, authorisation and the shape on the wire. [T-2] #22

Business rules are tested in `unit_test/service/test_trade*.py` without HTTP.
What is asserted here is what the controller owns: who may call the route,
what the body is allowed to contain, which status code each refusal carries,
and what the JSON looks like.

**This is the ledger's first write route**, and most of this file exists
because of that. Every route on this service until now has been a GET, and
`controller/routes.py` says in its own docstring that there is deliberately no
POST — "a write route that trusted a trader's own token would be a route for
minting yourself credits". The amendment on ADR 0009 is the answer, and it is
an answer about the *request body*: the route takes no account, no amount and
no leg, so the debited account is not an input and the amount is not an input.
That claim is only true if the body is refused when it tries to name one,
which is what `test_a_body_naming_an_account_is_refused` holds.

**Two things about the 422s.** FastAPI's own validation — a missing
`state_version`, a quantity at five decimal places, `side: "sell"`, an unknown
field — does not pass through `controller/errors.py`, so those responses carry
`{"detail": [...]}` rather than the `{"error": {"code": ...}}` envelope every
other service uses. That is pre-existing: the preview route has behaved this
way since [T-1] #21. These tests assert the **status only** and deliberately
do not pin the body, so that a later ticket unifying the envelope does not
have to edit them.

**The cold path is stubbed at `market_terms.fetch` rather than at a
transport**, the same way `test_snapshot_routes.py` and
`test_preview_routes.py` do it: a route cannot be handed an
`httpx.MockTransport`. The service-layer tests exercise the real request
building through one; what is left here is whether a domain error raised below
the controller becomes the right status and the right code.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import bearer, mint_token
from unit_test.trade_fixtures import (
    B,
    Q,
    QUANTITY,
    SUBSIDY,
    Recorder,
    expected_total,
    unaffordable,
)

_CLIENT_KEY = "client-key-1"

# Every field in a successful response, and nothing else. Asserted as a set so
# that a gained field is a failure rather than an unnoticed addition — a
# replay has to return a byte-identical body, and a field the fresh path adds
# and the replay path does not is the way that breaks.
_FIELDS = {
    "transaction_id",
    "user_id",
    "market_id",
    "outcome_id",
    "side",
    "quantity",
    "total",
    "state_version",
}


def _books():
    from service import books  # noqa: PLC0415

    return books


def _entities():
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    from core import errors  # noqa: PLC0415

    return errors


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/trades"


class _Market:
    """A published market with a book and outcomes."""

    def __init__(self, *, status: str = "open", outcomes: int = 2) -> None:
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.status = status

    @property
    def transport(self):
        import httpx  # noqa: PLC0415

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": str(self.market_id),
                    "status": self.status,
                    "close_time": "2027-01-05T12:00:00Z",
                    "resolution_time": "2027-01-20T12:00:00Z",
                    "liquidity_b": str(B),
                    "seed_subsidy": str(SUBSIDY),
                    "published_at": "2026-09-01T09:00:00Z",
                    "outcomes": [
                        {"id": str(o), "position": i, "label": f"Outcome {i}"}
                        for i, o in enumerate(self.outcomes)
                    ],
                },
            )

        return httpx.MockTransport(handler)


async def _warm(
    session: AsyncSession, market: _Market, q: Sequence[Decimal] = tuple(Q)
) -> None:
    """Open the book and write `q`, committed, so the route's own session
    sees it."""
    await _books().ensure_open(
        session,
        market.market_id,
        access_token=mint_token(uuid.uuid4()),
        transport=market.transport,
    )
    outcome = _entities().MarketOutcome
    for position, value in enumerate(q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == market.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


class _Terms:
    """A stub for `market_terms.fetch`, patched onto the module.

    Patched there rather than onto `books` or `market_status`, because both
    hold the module and resolve `fetch` at call time — so this is the same
    function the real path calls, reached the same way.
    `test_snapshot_routes.py` uses the identical shape.
    """

    def __init__(self, *, raises: Exception | None = None, status: str = "open") -> None:
        self.calls = 0
        self.status = status
        self._raises = raises

    def install(self, monkeypatch: pytest.MonkeyPatch, market: _Market):
        from service import market_terms  # noqa: PLC0415

        async def fake(market_id, *, access_token, transport=None):  # noqa: ANN001
            self.calls += 1
            if self._raises is not None:
                raise self._raises
            return market_terms.MarketTerms(
                market_id=market_id,
                liquidity_b=B,
                seed_subsidy=SUBSIDY,
                published_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
                outcomes=[
                    market_terms.OutcomeTerms(outcome_id=o, position=i)
                    for i, o in enumerate(market.outcomes)
                ],
                status=self.status,
            )

        monkeypatch.setattr(market_terms, "fetch", fake)
        return self


@pytest.fixture
async def trade_client(clean_database):
    """A client whose app has a Redis stand-in bound to it.

    `httpx`'s `ASGITransport` does not run the lifespan, so `app.state.redis`
    — which `main.py` opens at startup — does not exist under test. The
    dependency is overridden rather than the attribute set, because the
    override is the seam the route actually reads through, and a test that
    poked `app.state` would keep passing if the route stopped using
    `get_redis` at all.

    Yields the client and the recorder, so a controller test can also assert
    that a refused request published nothing.
    """
    from controller.dependencies import get_redis  # noqa: PLC0415
    from main import create_app  # noqa: PLC0415

    app = create_app()
    recorder = Recorder()
    app.dependency_overrides[get_redis] = lambda: recorder

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, recorder


def _body(market: _Market, **overrides) -> dict:
    body = {
        "outcome_id": str(market.outcomes[0]),
        "side": "buy",
        "quantity": str(QUANTITY),
        "state_version": 0,
        "idempotency_key": _CLIENT_KEY,
    }
    body.update(overrides)
    return body


# =========================================================================
# Authentication
# =========================================================================
async def test_a_trade_needs_a_token(
    trade_client, session: AsyncSession
) -> None:
    """401, and the token is not optional anywhere on this service. The
    debited account comes from the `sub` claim, so a route reachable without
    one is a route with no account to debit."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)

    response = await client.post(_path(market.market_id), json=_body(market))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_may_trade(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any valid access token, any role. A trade is what a trader is for, so
    there is no admin gate — and the account debited is the caller's own,
    which is what makes a trader's own token the right credential here."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 201


# =========================================================================
# The response
# =========================================================================
async def test_a_successful_trade_is_201_with_the_documented_fields(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """201, because this creates a transaction and the ledger has no POST
    route to match against — `market_service` answers 200 to all six of its
    own, and `auth_service` answers 201 to `register`, so there is no house
    style to inherit and this follows the one that describes what happened.

    The field set is exact. A replay has to return a byte-identical body, so
    a field the fresh path grows and the replay path does not is a contract
    break that shows up only on a retry.
    """
    client, _ = trade_client
    market = _Market()
    user_id = uuid.uuid4()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market),
        headers=bearer(user_id),
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == _FIELDS
    assert body["user_id"] == str(user_id)
    assert body["market_id"] == str(market.market_id)
    assert body["outcome_id"] == str(market.outcomes[0])
    assert body["side"] == "buy"
    assert body["state_version"] == 1


async def test_the_amounts_are_decimal_strings_and_the_version_is_a_number(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the rest of this service already follows: money crosses as an
    exact decimal string, because a JSON number is an IEEE double by the time
    a browser has parsed it and this is what somebody was charged.
    `state_version` is a JSON number, because it is a count and not money."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    body = (
        await client.post(
            _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
        )
    ).json()

    assert isinstance(body["total"], str)
    assert isinstance(body["quantity"], str)
    assert isinstance(body["state_version"], int)
    assert Decimal(body["total"]) < 0, "a buy takes credits out of the trader"
    assert Decimal(body["total"]).as_tuple().exponent == -4


async def test_the_response_carries_no_prices(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replayed price was true once and is a lie afterwards, unlike `total`,
    which is what the trader was charged for ever. Prices are the realtime
    contract's — the `price` frame, or the snapshot route beside this one."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    # Asserted before the field check, and not decoration: an error body has
    # no price field either, so without this the test passes green against a
    # route that does not exist.
    assert response.status_code == 201
    body = response.json()
    assert not [k for k in body if "price" in k.lower()], body


async def test_a_retry_returns_the_same_status_and_the_same_body(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry is answered with what the original was answered with, down to
    the status code. A `200` on the replay and a `201` on the original would
    let a client tell the two apart, which is the one thing an idempotent
    endpoint is not supposed to make visible."""
    client, _ = trade_client
    market = _Market()
    user_id = uuid.uuid4()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)
    headers = bearer(user_id)

    first = await client.post(_path(market.market_id), json=_body(market), headers=headers)
    second = await client.post(_path(market.market_id), json=_body(market), headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


# =========================================================================
# The body: what it may not contain
# =========================================================================
@pytest.mark.parametrize(
    "extra",
    [
        {"account_id": str(uuid.uuid4())},
        {"amount": "1.0000"},
        {"total": "-1.0000"},
        {"legs": [{"account_id": str(uuid.uuid4()), "amount": "1.0000"}]},
    ],
    ids=["account_id", "amount", "total", "legs"],
)
async def test_a_body_naming_an_account_or_an_amount_is_refused(
    trade_client,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict,
) -> None:
    """Rejected, not ignored, and the difference is the whole of ADR 0009's
    amendment.

    "It accepts no account, no amount and no leg" is a claim about what the
    ledger builds from, and Pydantic's default is `extra="ignore"` — which
    would make that claim true while telling a client that named an account
    its request had succeeded. `extra="forbid"` is what turns the sentence
    into a refusal. `total` is in the list because it is the field a client
    would most plausibly echo back from a preview, and it must not be read.
    """
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, **extra),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422


async def test_the_state_version_is_required(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required, not optional with a default.

    An optional staleness field lets a client silently opt out of the only
    staleness protection the trade has — and the client most likely to omit
    it is the one that never read the preview's `state_version` in the first
    place, which is exactly the client that should not be trading.
    """
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    body = _body(market)
    del body["state_version"]

    response = await client.post(
        _path(market.market_id), json=body, headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 422


@pytest.mark.parametrize("missing", ["outcome_id", "side", "quantity", "idempotency_key"])
async def test_every_other_field_is_required_too(
    trade_client,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """Five fields, all of them. None has a defensible default: an absent
    `idempotency_key` in particular would make a retry a second trade."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    body = _body(market)
    del body[missing]

    response = await client.post(
        _path(market.market_id), json=body, headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 422


@pytest.mark.parametrize("quantity", ["0", "-1", "-0.0001"])
async def test_a_quantity_of_zero_or_less_is_refused(
    trade_client,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    quantity: str,
) -> None:
    """`> 0`, the same bound the preview's query parameter carries. A trade of
    nothing costs nothing and writes two zero-amount entries, which
    `ck_entries_amount_nonzero` refuses at the database — a 500 for something
    the route can see is wrong."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, quantity=quantity),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422


async def test_a_quantity_with_five_decimal_places_is_refused(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-038's rule, on the write path this time.

    Rounding `10.00005` to `10.0001` would charge for a quantity the trader
    did not type, and the discrepancy would surface as a balance that moved by
    the wrong amount with no error anywhere to explain it. The preview refuses
    it, so a trade that accepted it could not have been previewed.
    """
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, quantity="10.00005"),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422


async def test_the_route_refuses_a_sell(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Buy only, until [T-3] #23.

    A sell needs the per-user holdings check read under the book lock, and
    there are no positions to check against until this ticket has written
    some. A sell route without that check is a route for selling shares you do
    not hold. The service function underneath already takes a `Side` and is
    generic — that is deliberate, and it is why this refusal lives at the
    route rather than in the money path.
    """
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell"),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422


# =========================================================================
# The refusals that carry a code
# =========================================================================
async def test_a_closed_market_is_409_market_closed(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spelled the same way market_service spells its own version of the same
    refusal, so a frontend error handler built for one serves both."""
    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms(status="closed").install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_closed"
    assert recorder.calls == []


async def test_a_stale_quote_is_409_with_the_versions_in_details(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quote_stale` carries `quoted` and `current` in `error.details`.

    The envelope's `details` key exists for exactly this: a refused trade has
    to tell somebody something they can act on, and re-deriving it from the
    prose of `message` is not an API. `insufficient_funds` already sets the
    precedent with `balance` and `required`.
    """
    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, state_version=7),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "quote_stale"
    assert error["details"] == {"quoted": 7, "current": 0}
    assert recorder.calls == []


async def test_an_unaffordable_trade_is_409_insufficient_funds(
    trade_client,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    starting_credits: Decimal,
) -> None:
    """409 rather than 422: nothing about the request is malformed and the
    same request may well succeed later. The existing `details` with `balance`
    and `required` are what let [FE] #49 say how short somebody was."""
    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)
    quantity = unaffordable(starting_credits)
    assert -expected_total(Q, 0, "buy", quantity) > starting_credits

    response = await client.post(
        _path(market.market_id),
        json=_body(market, quantity=str(quantity)),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_funds"
    assert set(error["details"]) == {"balance", "required"}
    assert recorder.calls == []


async def test_an_outcome_from_another_market_is_422_unknown_outcome(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """422 rather than 404: the market was found and it is the parameter that
    is wrong. 404 already means "no such market" on this service, and a client
    could not tell two different bugs apart if both used it."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, outcome_id=str(uuid.uuid4())),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_outcome"


async def test_an_unreachable_market_service_is_503(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate fails closed. A sick dependency is never read as an open
    market, and 503 says there is no usable answer rather than inventing
    one."""
    client, _ = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms(raises=_errors().MarketTermsUnavailable()).install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "market_terms_unavailable"


async def test_a_market_that_does_not_exist_is_404(
    trade_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold market the upstream does not know. Not distinguished from a
    draft or a submitted one, because `get_published` on the other side makes
    those three indistinguishable on purpose."""
    client, _ = trade_client
    market = _Market()
    _Terms(raises=_errors().MarketNotFound()).install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_a_malformed_market_id_in_the_path_is_422(
    trade_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path parameter is a UUID, and a string that is not one never
    reaches the service layer at all."""
    client, _ = trade_client
    market = _Market()

    response = await client.post(
        "/ledger/markets/not-a-uuid/trades",
        json=_body(market),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422


# =========================================================================
# The contract at /docs
# =========================================================================
async def test_the_route_is_documented_as_a_post_that_creates(
    trade_client,
) -> None:
    """`/docs` is the contract with the frontend, and this is the first write
    route on it. The status code and the path are part of that contract, so
    they are asserted against the generated schema rather than only against a
    live response — a route whose declared `status_code` disagrees with what
    it returns would be a document that lies."""
    client, _ = trade_client

    schema = (await client.get("/openapi.json")).json()
    path = schema["paths"]["/ledger/markets/{market_id}/trades"]

    assert "post" in path
    assert "201" in path["post"]["responses"]


# =========================================================================
# Reconciled with dev after #108 and #110
# =========================================================================
async def test_a_quantity_wider_than_the_column_is_422_not_500(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview's digit ceiling, on the write path: 18 digits in all, the
    width of `Numeric(18, 4)`.

    `1E+25` has no fifth decimal place and is greater than zero, so `gt=0`
    and `decimal_places=4` both let it through. Past them it reaches the
    pricing path, where quantizing a value that wide raises
    `InvalidOperation` — an unmapped 500 for a body the route can see is
    wrong.
    """
    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, quantity="1E+25"),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422
    assert recorder.calls == []


async def test_an_incomplete_book_is_500_market_book_incomplete(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the envelope, with the code, and with nothing published. The
    service-layer tests cover every damaged shape and the rows left behind;
    this holds only that the refusal leaves the route as the mapped 500."""
    from unit_test.conftest import strip_outcomes  # noqa: PLC0415

    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    await strip_outcomes(session, market.market_id)
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id), json=_body(market), headers=bearer(uuid.uuid4())
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "market_book_incomplete"
    assert recorder.calls == []


async def test_the_published_prices_are_the_snapshot_routes_strings(
    trade_client, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both docs pages promise a client the same price strings from the
    `price` frame and from `GET .../snapshot` for the same state, and that
    holds because both go through `book_prices.priced` and
    `core/pricing.py::quantize_price`, not because two copies agree.

    Asserted end to end: the event the trade published, against the snapshot
    route's body read straight after it — the same post-trade state.
    """
    import json  # noqa: PLC0415

    client, recorder = trade_client
    market = _Market()
    await _warm(session, market)
    _Terms().install(monkeypatch, market)
    headers = bearer(uuid.uuid4())

    traded = await client.post(
        _path(market.market_id), json=_body(market), headers=headers
    )
    assert traded.status_code == 201

    snapshot = await client.get(
        f"/ledger/markets/{market.market_id}/snapshot", headers=headers
    )
    assert snapshot.status_code == 200

    event = json.loads(recorder.calls[0][1])
    body = snapshot.json()
    assert event["state_version"] == body["state_version"] == 1
    assert event["prices"] == body["prices"]
    assert event["occurred_at"] == body["occurred_at"]


async def test_the_trade_contract_does_not_offer_market_not_published(
    trade_client,
) -> None:
    """`market_not_published` cannot reach a caller of this route: the gate
    runs first, and market_service's public detail route answers 404 for a
    draft or a submitted market, so an unpublished market is
    `market_not_found` before `books.ensure_open` ever reads `published_at`.
    `test_preview_routes.py` holds the same for the preview; this is the
    trade route's copy. Asserted on the code string, not on prose.
    """
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    client, _ = trade_client
    operation = (await client.get("/openapi.json")).json()["paths"][
        "/ledger/markets/{market_id}/trades"
    ]["post"]
    assert "market_not_published" not in json.dumps(operation).lower()

    doc = (Path(__file__).resolve().parents[4] / "docs/api/ledger-service.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("## POST /ledger/markets/{market_id}/trades", 1)[1]
    section = section.split("\n## ", 1)[0]
    assert "market_not_published" not in section
